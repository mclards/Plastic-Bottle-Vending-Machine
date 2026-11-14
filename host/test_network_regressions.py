"""Command-boundary gateway failure tests; no system commands are executed."""
import unittest
from unittest.mock import patch
import gateway_network as network


class NetworkRegression(unittest.TestCase):
    def setUp(self):
        network._shaped.clear();network._pairs.clear();network._isp_blocked.clear();network._licensed=True
        self.calls=[]
        self.mock=patch.object(network,'run',side_effect=self.fake_run)
        self.mock.start();self.addCleanup(self.mock.stop)

    def fake_run(self,args,check=True):
        self.calls.append(args)
        return type('Result',(),{'returncode':0,'stderr':''})()

    def test_zero_credit_never_installs_authorization(self):
        self.assertFalse(network.grant('10.0.0.2','02:00:00:00:00:01',0,1024,512))
        self.assertFalse(any(c[:2]==['ipset','add'] for c in self.calls))

    def test_broadcast_is_rejected(self):
        with self.assertRaises(ValueError):network.grant('10.0.31.255','02:00:00:00:00:01',15,1024,512)
        self.assertFalse(self.calls)

    def test_failed_shaper_never_grants_and_retry_cleans_partial_filters(self):
        def fail(args,check=True):
            self.fake_run(args,check)
            if args[:3]==['tc','class','replace'] and 'ifb0' in args:raise RuntimeError('injected shaping failure')
            return type('Result',(),{'returncode':0,'stderr':''})()
        with patch.object(network,'run',side_effect=fail):
            with self.assertRaises(RuntimeError):network.grant('10.0.0.2','02:00:00:00:00:01',15,1024,512)
        self.assertFalse(any(c[:2]==['ipset','add'] for c in self.calls))
        self.assertNotIn('10.0.0.2',network._shaped)
        self.assertTrue(network.grant('10.0.0.2','02:00:00:00:00:01',15,1024,512))
        self.assertTrue(any(c[:3]==['tc','filter','del'] and 'eth1' in c for c in self.calls))

    def test_failed_auth_install_removes_partial_pair(self):
        def fail(args,check=True):
            self.fake_run(args,check)
            if args[:3]==['ipset','add','ecofi_auth']:raise RuntimeError('injected authorization failure')
            return type('Result',(),{'returncode':0,'stderr':''})()
        with patch.object(network,'run',side_effect=fail):
            with self.assertRaises(RuntimeError):network.grant('10.0.0.2','02:00:00:00:00:01',15,1024,512)
        self.assertIn(['ipset','del','ecofi_pairs','10.0.0.2,02:00:00:00:00:01','-exist'],self.calls)
        self.assertFalse(network._pairs)

    def test_license_flush_failure_is_retried(self):
        with patch.object(network,'ipt',return_value=type('Result',(),{'returncode':1})()):
            with patch.object(network,'run',side_effect=RuntimeError('injected flush failure')):
                with self.assertRaises(RuntimeError):network.set_license(False)
                self.assertIsNone(network._licensed)
            network.set_license(False)
        self.assertFalse(network._licensed)
        self.assertIn(['ipset','flush','ecofi_auth'],self.calls)

    def test_lease_cannot_exceed_kernel_cap(self):
        network.grant('10.0.0.2','02:00:00:00:00:01',600,1024,512)
        self.assertIn(['ipset','add','ecofi_auth','10.0.0.2','timeout','30','-exist'],self.calls)

    def test_isp_gateway_access_never_bypasses_auth_chain(self):
        network.set_isp_gateway_access(False,'192.168.100.1')
        self.assertEqual(network._isp_blocked,{'192.168.1.1','192.168.100.1'})
        self.assertTrue(any('ECOFI_FORWARD' in c and '-I' in c and '192.168.100.1/32' in c and 'DROP' in c for c in self.calls))
        self.assertFalse(any('ECOFI_FORWARD' in c and ('-I' in c or '-A' in c) and 'ACCEPT' in c for c in self.calls))
        self.calls.clear()
        network.set_isp_gateway_access(True,'192.168.1.1')
        self.assertEqual(network._isp_blocked,set())
        self.assertTrue(any('ECOFI_FORWARD' in c and '-D' in c and '192.168.100.1/32' in c and 'DROP' in c for c in self.calls))
        self.assertFalse(any('ECOFI_FORWARD' in c and ('-I' in c or '-A' in c) and 'ACCEPT' in c for c in self.calls))

    def test_gaming_qos_scopes_interfaces_directions_and_cleans_prio(self):
        network.set_gaming_qos(True,percent=25)
        self.assertTrue(any('ECOFI_QOS' in c and '-i' in c and network.LAN in c and '--dports' in c and network._GAME_UDP_PORTS in c for c in self.calls))
        self.assertTrue(any('ECOFI_QOS' in c and '-i' in c and network.WAN in c and '--sports' in c and network._GAME_UDP_PORTS in c for c in self.calls))
        self.assertFalse(any('ECOFI_QOS' in c and network.WAN in c and '--dports' in c for c in self.calls))
        self.assertTrue(any(c[:5]==['tc','filter','add','dev',network.LAN] and 'fw' in c and '1:10' in c for c in self.calls))
        self.assertTrue(any(c[:5]==['tc','filter','add','dev','ifb0'] and 'u32' in c and '1:10' in c for c in self.calls))
        self.assertFalse(any(c[:5]==['tc','filter','add','dev','ifb0'] and 'fw' in c for c in self.calls))
        self.assertTrue(any(c[:5]==['tc','class','replace','dev','ifb0'] and '25mbit' in c for c in self.calls))
        self.calls.clear()
        network.set_gaming_qos(False)
        for device in [network.LAN,'ifb0']:
            self.assertIn(['tc','filter','del','dev',device,'protocol','ip','parent','1:','prio','1'],self.calls)
            self.assertIn(['tc','class','del','dev',device,'parent','1:1','classid','1:10'],self.calls)


if __name__=='__main__':unittest.main()
