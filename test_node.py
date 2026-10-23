import copy
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
import json
import base64
from node import (ENC_PREFIX, PeerTracker, ProtocolError, Server, Store, MAX_PENDING,
                  _aad, _frame, _read_frame, _tag, decrypt_body, encrypt_body,
                  flush, flush_peers, offer)


def msg(id='m1', destination='C'):
    return {'id':id,'source':'A','destination':destination,'body':'Help needed',
            'priority':2,'expires_at':time.time()+60}


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a=Store(str(Path(self.tmp.name)/'a.db'),'A')
        self.b=Store(str(Path(self.tmp.name)/'b.db'),'B')
        self.c=Store(str(Path(self.tmp.name)/'c.db'),'C')
        self.servers=[]

    def server(self, store, key=None):
        server=Server(('127.0.0.1',0),store,key=key)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        self.servers.append(server)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address

    def test_custody_recovery_and_durable_restart(self):
        m=msg();self.assertEqual(self.a.receive(m),'stored')
        self.assertEqual(flush(self.a,('127.0.0.1',1)),[('m1','unreachable')])
        self.assertEqual(self.a.status()['pending'],1)
        bp=self.server(self.b)
        self.assertEqual(flush(self.a,bp),[('m1','stored')])
        self.assertEqual(self.a.status()['pending'],0)
        restarted=Store(str(Path(self.tmp.name)/'b.db'),'B')
        self.assertEqual(restarted.status()['pending'],1)
        cp=self.server(self.c)
        self.assertEqual(flush(restarted,cp),[('m1','delivered')])
        self.assertEqual(self.c.status()['delivered'],1)
        self.assertEqual(self.c.status()['pending'],0)

    def test_idempotent_ack_and_conflicting_id(self):
        m=msg();bp=self.server(self.b)
        self.assertEqual(offer(*bp,m,sender='A'),'stored')
        self.assertEqual(offer(*bp,m,sender='A'),'stored')  # accepted receipt
        self.assertEqual(offer(*bp,m,sender='C'),'duplicate')  # no receipt
        altered=copy.deepcopy(m);altered['body']='Different'
        self.assertEqual(offer(*bp,altered,sender='A'),'conflict')
        self.assertEqual(self.b.status()['pending'],1)

    def test_lost_ack_receipt_survives_restart_and_forwarding(self):
        m=msg(); self.a.receive(m)
        self.assertEqual(self.b.receive(m,sender='A'),'stored')  # ACK lost
        restarted=Store(str(Path(self.tmp.name)/'b.db'),'B')
        cp=self.server(self.c)
        self.assertEqual(flush(restarted,cp),[("m1","delivered")])
        bp=self.server(restarted)
        self.assertEqual(flush(self.a,bp),[("m1","stored")])
        self.assertEqual(self.a.status()['pending'],0)

    def test_duplicate_upstream_does_not_erase_custody(self):
        m=msg(); self.a.receive(m); self.b.receive(m)
        ap=self.server(self.a)
        self.assertEqual(flush(self.b,ap),[("m1","duplicate")])
        self.assertEqual(self.b.status()["pending"],1)
        cp=self.server(self.c)
        self.assertEqual(flush(self.b,cp),[("m1","delivered")])
        self.assertEqual(self.b.status()["pending"],0)

    def test_invalid_and_expired_messages(self):
        bp=self.server(self.b)
        m=msg();m['priority']=5
        with self.assertRaises(ValueError):offer(*bp,m,sender='A')
        m=msg();m['expires_at']=time.time()-1
        self.assertEqual(offer(*bp,m,sender='A'),'expired')
        self.assertEqual(self.b.status()['pending'],0)
        with socket.create_connection(bp) as sock:
            sock.sendall(b'{' + b'x'*9000 + b'\n')
            data=_read_frame(sock)
            self.assertIn('error',data)

    def test_full_buffer_does_not_poison_seen_id(self):
        from unittest.mock import patch
        with patch('node.MAX_PENDING', 1):
            self.assertEqual(self.b.receive(msg('first', 'C'), sender='A'), 'stored')
            self.assertEqual(self.b.receive(msg('second', 'C'), sender='A'), 'full')
            self.assertEqual(self.b.status()['pending'], 1)
            self.b.release('first')
            self.assertEqual(self.b.receive(msg('second', 'C'), sender='A'), 'stored')

    def test_parallel_store_instances_share_capacity_and_id_decision(self):
        from unittest.mock import patch
        from concurrent.futures import ThreadPoolExecutor
        db_path = str(Path(self.tmp.name) / 'shared.db')
        first, second = Store(db_path, 'B'), Store(db_path, 'B')
        with patch('node.MAX_PENDING', 1), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda pair: pair[0].receive(pair[1], sender='A'),
                                     [(first, msg('one')), (second, msg('two'))]))
        self.assertEqual(sorted(outcomes), ['full', 'stored'])
        self.assertEqual(first.status()['pending'], 1)
        rejected = 'one' if outcomes[0] == 'full' else 'two'
        accepted = 'two' if rejected == 'one' else 'one'
        first.release(accepted)
        self.assertEqual(second.receive(msg(rejected), sender='A'), 'stored')

    def test_keyed_offer_roundtrip_and_forgery_rejected(self):
        key=b'lab-key'
        bp=self.server(self.b,key=key)
        self.assertEqual(offer(*bp,msg(),sender='A',key=key),'stored')
        forged=msg('m2')
        with self.assertRaises(ProtocolError):
            offer(*bp,forged,sender='A',key=b'wrong-key')
        with self.assertRaises(ProtocolError):
            offer(*bp,forged,sender='A')  # untagged against keyed server
        self.assertEqual(self.b.status()['pending'],1)  # only the legitimate one
        # Open-lab server still accepts untagged offers (documented compatibility).
        ap=self.server(self.a)
        self.assertEqual(offer(*ap,msg('m3','A'),sender='B'),'delivered')

    def test_unauthenticated_ack_rejected_and_custody_kept(self):
        key=b'lab-key'
        srv=socket.socket();srv.bind(('127.0.0.1',0));srv.listen(1)
        self.addCleanup(srv.close)
        port=srv.getsockname()[1]
        def fake():
            conn,_=srv.accept()
            _read_frame(conn)
            conn.sendall(_frame({'id':'m1','result':'stored'}))  # no tag
            conn.close()
        threading.Thread(target=fake,daemon=True).start()
        m=msg();self.a.receive(m)
        with self.assertRaises(ProtocolError):
            offer('127.0.0.1',port,m,sender='A',key=key)
        self.assertEqual(self.a.status()['pending'],1)  # custody retained

    def test_keyed_wire_hides_body_and_store_keeps_plaintext(self):
        key=b'lab-key'
        bp=self.server(self.b,key=key)
        m=msg()
        # Capture the exact bytes a keyed client puts on the wire.
        import socket as _s
        probe=_s.socket();probe.bind(('127.0.0.1',0));probe.listen(1)
        self.addCleanup(probe.close)
        captured={}
        def peer():
            conn,_=probe.accept()
            data=b''
            while not data.endswith(b'\n'):data+=conn.recv(1)
            captured['wire']=data
            frame=json.loads(data)
            self.assertTrue(frame['message']['body'].startswith(ENC_PREFIX))
            conn.sendall(_frame({'id':'m1','result':'stored','tag':_tag(key,{'id':'m1','result':'stored'})}))
            conn.close()
        threading.Thread(target=peer,daemon=True).start()
        offer('127.0.0.1',probe.getsockname()[1],m,sender='A',key=key)
        self.assertNotIn(b'Help needed',captured['wire'])
        self.assertNotIn(m['body'].encode(),captured['wire'])
        # Real server roundtrip: store ends up with readable plaintext.
        self.assertEqual(offer(*bp,m,sender='A',key=key),'stored')
        stored=self.b.pending()[0]
        self.assertEqual(stored['body'],m['body'])
        # The sender's own buffer was not mutated by encryption.
        self.assertEqual(m['body'],'Help needed')

    def test_keyed_server_rejects_plaintext_body(self):
        key=b'lab-key'
        bp=self.server(self.b,key=key)
        payload={'op':'offer','message':msg(),'sender':'A'}
        payload['tag']=_tag(key,payload)  # valid tag over plaintext body
        with socket.create_connection(bp) as sock:
            sock.sendall(_frame(payload))
            reply=_read_frame(sock)
        self.assertIn('error',reply)
        self.assertEqual(self.b.status()['pending'],0)

    def test_open_lab_rejects_encrypted_body(self):
        ap=self.server(self.a)  # no key
        payload={'op':'offer','message':msg(),'sender':'B'}
        payload['message']['body']=encrypt_body(b'lab-key','Help needed')
        with socket.create_connection(ap) as sock:
            sock.sendall(_frame(payload))
            reply=_read_frame(sock)
        self.assertIn('error',reply)
        self.assertEqual(self.a.status()['pending'],0)

    def test_tampered_ciphertext_fails_tag_check(self):
        key=b'lab-key'
        bp=self.server(self.b,key=key)
        payload={'op':'offer','message':msg(),'sender':'A'}
        body=payload['message']['body']=encrypt_body(key,'Help needed')
        mid=len(body)//2
        flip='A' if body[mid]!='A' else 'B'
        payload['message']['body']=body[:mid]+flip+body[mid+1:]
        payload['tag']=_tag(key,{'op':'offer','message':msg(),'sender':'A'})  # stale tag
        with socket.create_connection(bp) as sock:
            sock.sendall(_frame(payload))
            reply=_read_frame(sock)
        self.assertIn('error',reply)
        self.assertEqual(self.b.status()['pending'],0)

    def test_decrypt_roundtrip_and_wrong_key(self):
        key=b'lab-key'
        enc=encrypt_body(key,'x'*200)
        self.assertTrue(enc.startswith(ENC_PREFIX))
        self.assertEqual(decrypt_body(key,enc),'x'*200)
        self.assertNotEqual(encrypt_body(key,'x'*200),enc)  # fresh nonce per offer
        with self.assertRaises(ProtocolError):  # AEAD failure is deterministic
            decrypt_body(b'other-key',enc)

    def test_tampered_ciphertext_and_transplant_rejected(self):
        key=b'lab-key'
        m=msg()
        aad=_aad(m)
        enc=encrypt_body(key,m['body'],aad)
        raw=bytearray(base64.b64decode(enc[len(ENC_PREFIX):]))
        raw[-1]^=1  # flip one ciphertext bit
        tampered=ENC_PREFIX+base64.b64encode(bytes(raw)).decode()
        with self.assertRaises(ProtocolError):
            decrypt_body(key,tampered,aad)
        with self.assertRaises(ProtocolError):  # truncated tag/ciphertext
            decrypt_body(key,ENC_PREFIX+base64.b64encode(bytes(raw[:20])).decode(),aad)
        other=dict(m);other['destination']='B'
        with self.assertRaises(ProtocolError):  # metadata transplant bound by AAD
            decrypt_body(key,enc,_aad(other))
        self.assertEqual(decrypt_body(key,enc,aad),m['body'])

    def test_replayed_offer_frame_rejected_custody_unaffected(self):
        key=b'lab-key'
        bp=self.server(self.b,key=key)
        m=msg()
        wire=dict(m)
        wire['body']=encrypt_body(key,wire['body'],_aad(wire))
        payload={'op':'offer','message':wire,'sender':'A'}
        payload['tag']=_tag(key,payload)
        frame=_frame(payload)
        with socket.create_connection(bp) as sock:  # first send accepted
            sock.sendall(frame)
            self.assertEqual(_read_frame(sock).get('result'),'stored')
        with socket.create_connection(bp) as sock:  # byte-exact replay rejected
            sock.sendall(frame)
            self.assertIn('error',_read_frame(sock))
        self.assertEqual(self.b.status()['pending'],1)
        # A legitimate retry makes a fresh ciphertext and is still accepted.
        self.assertEqual(offer(*bp,m,sender='A',key=key),'stored')
        self.assertEqual(self.b.status()['pending'],1)

    def test_peer_tracker_prefers_reachable_peer(self):
        tracker=PeerTracker(rate=0.5)
        self.assertEqual([p for p in tracker.order([('127.0.0.1',1),('127.0.0.1',2)])],
                         tracker.order([('127.0.0.1',2),('127.0.0.1',1)]))  # unknowns tie-break by name
        dead=('127.0.0.1',1)
        cp=self.server(self.c)
        self.a.receive(msg())
        outcomes=flush_peers(self.a,[dead,cp],tracker)
        self.assertEqual(sorted((mid,res) for mid,res,_ in outcomes),
                         [('m1','delivered'),('m1','unreachable')])
        self.assertLess(tracker.score(dead),tracker.score(cp))
        self.assertEqual(tracker.order([dead,cp])[0],cp)
        self.assertEqual(self.c.status()['delivered'],1)
        self.assertEqual(self.a.status()['pending'],0)

    def test_transfer_log_records_observed_custody_events(self):
        key=b'lab-key'
        cp=self.server(self.c,key=key)
        self.a.receive(msg(),sender=None)  # local injection logged too
        outcomes=flush_peers(self.a,[cp],PeerTracker(),key=key)
        self.assertEqual(outcomes,[('m1','delivered',cp)])
        out_log=self.a.transfers()
        kinds=[(e['direction'],e['message_id'],e['peer'],e['result']) for e in out_log]
        self.assertIn(('out','m1',f'{cp[0]}:{cp[1]}','delivered'),kinds)
        self.assertIn(('in','m1','local','stored'),kinds)
        detail=__import__('json').loads(next(e['detail'] for e in out_log if e['direction']=='out'))
        self.assertEqual(detail['rank'],0)
        self.assertIn('untried peer',detail['basis'])
        in_log=self.c.transfers()
        self.assertEqual([(e['direction'],e['peer'],e['result']) for e in in_log],
                         [('in','A','delivered')])
        self.assertEqual(self.c.delivered_recent()[0]['id'],'m1')
        # EMA basis becomes visible after a real outcome
        tracker=PeerTracker()
        self.a.receive(msg('m2'))
        flush_peers(self.a,[cp],tracker,key=key)
        self.b.receive(msg('m3'))
        flush_peers(self.b,[('127.0.0.1',1),cp],tracker,key=key)  # one dead, one live
        second=__import__('json').loads([e for e in self.b.transfers()
                                         if e['direction']=='out'][0]['detail'])
        self.assertEqual(second['rank'],0)
        self.assertIn('EMA',second['basis'])  # live peer ranked first on real score

    def test_priority_order_and_expiration(self):
        lo=msg('low');lo['priority']=0
        hi=msg('high');hi['priority']=2
        self.a.receive(lo);self.a.receive(hi)
        self.assertEqual([m['id'] for m in self.a.pending()],['high','low'])
        stale=msg('stale');stale['expires_at']=time.time()+.01
        self.a.receive(stale);time.sleep(.02)
        self.assertNotIn('stale',[m['id'] for m in self.a.pending()])


if __name__=='__main__':unittest.main()
