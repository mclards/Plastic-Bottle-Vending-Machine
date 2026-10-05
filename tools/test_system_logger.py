#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for ChuteSequenceTracker & SystemLogger (v2.3.18 sequence flow).
"""

import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))
from system_logger import SystemLogger, ChuteSequenceTracker


class TestChuteFlowAndLogger(unittest.TestCase):

    def setUp(self):
        self.logger = SystemLogger()
        self.tracker = ChuteSequenceTracker(self.logger)

    def test_default_stages_v2318(self):
        st = self.tracker.stages
        keys = list(st.keys())
        expected = ['1_gate', '2_intake', '3_scale', '4_nir', '5_exit', '6_drop']
        self.assertEqual(keys, expected)
        self.assertEqual(st['3_scale']['title'], 'Scale (HX711)')
        self.assertEqual(st['4_nir']['title'], 'NIR Spectrometer')

    def test_full_simulation_flow(self):
        self.tracker.simulate_scan(is_pet=True, weight=35.0, cal_w=48.5, delay_ms=50)
        time.sleep(0.5)

        state = self.tracker.get_state()
        self.assertEqual(state['status'], 'PASSED')
        self.assertEqual(state['current_stage'], 6)
        self.assertTrue(state['stages']['3_scale']['is_valid'])
        self.assertTrue(state['stages']['4_nir']['is_pet'])
        self.assertTrue(state['stages']['6_drop']['transit_verified'])

        # Check log entries exist
        logs = state['events']
        self.assertGreater(len(logs), 0)
        # Ensure fine timestamp format
        first_log = logs[-1]
        self.assertTrue('ts' in first_log or 'timestamp' in first_log)

    def test_weight_reject_flow(self):
        self.tracker.simulate_scan(is_pet=True, weight=8.0, cal_w=48.5, delay_ms=50)
        time.sleep(0.4)

        state = self.tracker.get_state()
        self.assertEqual(state['status'], 'REJECTED')
        self.assertEqual(state['stages']['3_scale']['status'], 'failed')
        self.assertFalse(state['stages']['3_scale']['is_valid'])


if __name__ == '__main__':
    unittest.main()
