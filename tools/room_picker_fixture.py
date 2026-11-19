"""Loopback fixture for the room page browser test; never contacts HA, a Supervisor or a controller.

Runs the app's real ingress handler and room page with the synthetic fakes of
hargassner-dashboard/test_room_picker.py on 127.0.0.1, with room learning
over the synthetic history of test_room_learning.py. Prints "PORT <n>",
then one "SAVED <json>" line per options POST the page makes. Ends when stdin
closes.
"""
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hargassner-dashboard'))

import ingress_proxy  # noqa: E402
import room_learning as rl  # noqa: E402
import room_picker as rp  # noqa: E402
import run_app  # noqa: E402
import test_room_learning as history  # noqa: E402
import test_room_picker as fixtures  # noqa: E402

ROOMS = [('wohnzimmer', 'Wohnzimmer Temperatur', 'Wohnzimmer', '21.5'),
         ('kueche', 'Küche Temperatur', 'Küche', '20.0'),
         ('schlafzimmer', 'Schlafzimmer Thermometer', 'Schlafzimmer', '18.5'),
         ('bad', 'Bad Temperatur', 'Bad', '23.0'),
         ('kinderzimmer', 'Kinderzimmer', 'Kinderzimmer', '20.5'),
         ('arbeitszimmer_fensterseite_sehr_langer_name', 'Arbeitszimmer an der Fensterseite mit sehr langem Namen',
          '', '19.5'),
         ('keller', 'Keller Temperatur', 'Keller', '11.0'),
         ('garage', 'Garage', 'Garage', 'unavailable'),
         ('aussen', 'Außentemperatur Balkon', 'Balkon', '-2.5')]


def main():
    states = [{'entity_id': f'sensor.{entity}', 'state': value,
               'attributes': {'device_class': 'temperature', 'unit_of_measurement': '°C', 'friendly_name': name}}
              for entity, name, _area, value in ROOMS]
    world = fixtures.PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer', 'sensor.alter_sensor'],
                                  'kuehlere_raeume': ['sensor.wohnzimmer']}, states=states)
    world.areas = {f'sensor.{entity}': area for entity, _name, area, _value in ROOMS if area}
    # Room learning: the Wohnzimmer's last 14 days as a cool room warmed by sun (a warning to confirm).
    clock = history.Clock()
    recorded = history.History(history.sun_room, 14, end=clock.wall)
    world.picker.learning = rl.RoomLearning(
        token_source=lambda: 'synthetic-supervisor-token', clock=clock, kinds=lambda: history.KINDS,
        api_factory=lambda token, entities: history.FakeLearningApi(recorded, token, entities))
    original = fixtures.FakePickerApi.save_options

    def save(api, options):
        original(api, options)
        print('SAVED ' + json.dumps(rp.house_part(options), sort_keys=True), flush=True)
    settings = ingress_proxy.Settings('unused.example', 443, 'unused.example', 'i' * 43)
    with patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.1'), \
            patch.object(fixtures.FakePickerApi, 'save_options', save):
        server = run_app.MonitorProxyServer(('127.0.0.1', 0), settings, run_app.disabled_status,
                                            world.picker.page, world.picker.save, world.picker.learn)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # Stand-in for the worker thread: it takes over a saved selection.
        world.picker.apply_wait = 2
        idle = threading.Event()

        def worker():
            while not idle.wait(.05):
                world.monitor._apply_pending()
        threading.Thread(target=worker, daemon=True).start()
        print('PORT', server.server_address[1], flush=True)
        sys.stdin.read()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
