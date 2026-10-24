#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for ChuteSequenceTracker & SystemLogger (v2.3.19 7-stage sequence flow).
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

    def test_default_stages_v2319(self):
        st = self.tracker.stages
        keys = list(st.keys())
        expected = ['1_gate', '2_intake', '3_prox', '4_scale', '5_nir', '6_exit', '7_drop']
        self.assertEqual(keys, expected)
        self.assertEqual(st['3_prox']['title'], 'Inductive Sensor (LJ12A3)')
        self.assertEqual(st['4_scale']['title'], 'Scale (HX711)')
        self.assertEqual(st['5_nir']['title'], 'NIR Spectrometer')

    def test_full_simulation_flow(self):
        self.tracker.simulate_scan(is_pet=True, is_metal=False, weight=35.0, cal_w=48.5, delay_ms=50)
        time.sleep(0.6)

        state = self.tracker.get_state()
        self.assertEqual(state['status'], 'PASSED')
        self.assertEqual(state['current_stage'], 7)
        self.assertFalse(state['stages']['3_prox']['is_metal'])
        self.assertTrue(state['stages']['4_scale']['is_valid'])
        self.assertTrue(state['stages']['5_nir']['is_pet'])
        self.assertTrue(state['stages']['7_drop']['transit_verified'])

        # Check log entries exist across CHUTE and SENSOR
        logs = state['events']
        self.assertGreater(len(logs), 0)
        # Ensure fine timestamp format
        first_log = logs[-1]
        self.assertTrue('ts' in first_log or 'timestamp' in first_log)

    def test_weight_reject_flow(self):
        self.tracker.simulate_scan(is_pet=True, is_metal=False, weight=8.0, cal_w=48.5, delay_ms=50)
        time.sleep(0.5)

        state = self.tracker.get_state()
        self.assertEqual(state['status'], 'REJECTED')
        self.assertEqual(state['stages']['4_scale']['status'], 'failed')
        self.assertFalse(state['stages']['4_scale']['is_valid'])

    def test_multi_category_filter(self):
        self.logger.info('CHUTE', 'Chute message')
        self.logger.measure('SENSOR', 'Sensor reading')
        self.logger.trigger('ACTUATOR', 'Actuator triggered')
        self.logger.info('SYSTEM', 'System boot')

        entries = self.logger.get_entries(category=['CHUTE', 'SENSOR', 'ACTUATOR'])
        categories = {e['category'] for e in entries}
        self.assertIn('CHUTE', categories)
        self.assertIn('SENSOR', categories)
        self.assertIn('ACTUATOR', categories)
        self.assertNotIn('SYSTEM', categories)

    def test_clean_gate_open_and_idle_reset(self):
        # 1. Simulate a scan that leaves sensor values
        self.tracker.simulate_scan(is_pet=True, is_metal=False, weight=35.0, cal_w=48.5, delay_ms=20)
        time.sleep(0.3)
        st1 = self.tracker.get_state()
        self.assertEqual(st1['stages']['4_scale']['status'], 'passed')

        # 2. Open gate for new bottle - must wipe previous sensor values clean!
        self.tracker.on_gate_open(session_id='test-sess-123')
        st2 = self.tracker.get_state()
        self.assertEqual(st2['status'], 'SCANNING')
        self.assertEqual(st2['current_stage'], 1)
        self.assertEqual(st2['stages']['4_scale']['detail'], '--')
        self.assertEqual(st2['stages']['5_nir']['detail'], '--')
        self.assertEqual(st2['stages']['7_drop']['detail'], '--')

        # 3. Close gate with no bottle inserted - must auto-reset to IDLE after 2s
        self.tracker.on_gate_closed()
        time.sleep(2.3)
        st3 = self.tracker.get_state()
        self.assertEqual(st3['status'], 'IDLE')
        self.assertEqual(st3['total_elapsed_ms'], 0)
        self.assertIsNone(st3['active_session_id'])


if __name__ == '__main__':
    unittest.main()
