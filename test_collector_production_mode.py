import os
import unittest
from unittest.mock import patch

import collector


class _Connector:
    def __init__(self, name):
        self.name = name


class CollectorProductionModeTests(unittest.TestCase):
    def test_render_runtime_detection(self):
        with patch.dict(os.environ, {"RENDER_SERVICE_ID": "srv-test"}, clear=True):
            self.assertTrue(collector._is_render_runtime())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(collector._is_render_runtime())

    def test_allowed_sources_excludes_browser_connectors(self):
        available = {
            "eBay": _Connector("eBay"),
            "SSENSE": _Connector("SSENSE"),
            "The Outnet": _Connector("The Outnet"),
            "Vinted": _Connector("Vinted"),
            "Grailed": _Connector("Grailed"),
        }
        with patch.dict(os.environ, {
            "LUXE_RADAR_COLLECTOR_ALLOWED_SOURCES": "eBay,SSENSE,The Outnet"
        }):
            sources = collector.ordered_sources(available)
        self.assertEqual({source.name for source in sources}, {"eBay", "SSENSE", "The Outnet"})

    def test_startup_seeds_can_be_disabled_without_disabling_user_queue(self):
        instance = collector.Collector()
        with patch.object(collector, "COLLECTOR_STARTUP_SEEDS_ENABLED", False), \
                patch.object(collector, "parse_seeds") as parse_seeds:
            self.assertEqual(instance._refill_defaults(), 0)
        parse_seeds.assert_not_called()
        instance._queue.append(("Balenciaga", 0.0))
        self.assertEqual(instance._dequeue(), ("Balenciaga", 0.0))


if __name__ == "__main__":
    unittest.main()
