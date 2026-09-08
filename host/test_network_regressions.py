"""Command-boundary gateway failure tests; no system commands are executed."""
import unittest
from unittest.mock import patch
import gateway_network as network


class NetworkRegression(unittest.TestCase):
    def setUp(self):
        network._shaped.clear();network._pairs.clear();network._licensed=True
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


if __name__=='__main__':unittest.main()
