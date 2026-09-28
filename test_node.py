import copy
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from node import Server, Store, _frame, _read_frame, flush, offer


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

    def server(self, store):
        server=Server(('127.0.0.1',0),store)
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

    def test_priority_order_and_expiration(self):
        lo=msg('low');lo['priority']=0
        hi=msg('high');hi['priority']=2
        self.a.receive(lo);self.a.receive(hi)
        self.assertEqual([m['id'] for m in self.a.pending()],['high','low'])
        stale=msg('stale');stale['expires_at']=time.time()+.01
        self.a.receive(stale);time.sleep(.02)
        self.assertNotIn('stale',[m['id'] for m in self.a.pending()])


if __name__=='__main__':unittest.main()
