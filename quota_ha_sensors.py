"""Publish existing quota snapshots as Home Assistant MQTT sensors."""
from datetime import datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

from quota_model import Snapshot, WEEK, FIVE_HOURS


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def fresh(observed, now):
    return now - 600 <= observed <= now + 60


def sensor_groups(snapshot, now, zone):
    """Return only normalized, non-secret quota values and stable entity keys."""
    groups = {}
    provider = snapshot.provider
    for window in snapshot.windows:
        key = provider + '_' + hashlib.sha256(window.key.encode()).hexdigest()[:16]
        name = ' '.join(part for part in (provider.title(), window.bucket, window.label) if part)
        observed = window.observed_at if window.observed_at is not None else snapshot.observed_at
        available = (fresh(snapshot.observed_at, now) and fresh(observed, now)
                     and not window.stale and not window.expired(now))
        values = {'available': available, 'used': window.used,
                  'remaining': max(0, 100 - window.used),
                  'reset': iso(window.resets_at), 'observed_at': iso(observed),
                  'budget': window.budget(now, zone) if available else None}
        fields = {'used': ('Used', '%'), 'remaining': ('Remaining', '%'),
                  'reset': ('Reset', 'timestamp')}
        if window.seconds in (WEEK, FIVE_HOURS):
            fields['budget'] = ('Pacing allowance', '%')
        groups[key] = (name, fields, values)
    if snapshot.reset_bank is not None:
        bank = snapshot.reset_bank
        groups[provider + '_bank'] = (
            provider.title(), {'count': ('Banked resets', 'count'),
                               'expiry': ('Banked reset expiry', 'timestamp')},
            {'available': fresh(snapshot.observed_at, now) and not bank.needs_refresh(now),
             'count': bank.available_count, 'expiry': iso(bank.next_expiry),
             'expiry_details_complete': bank.details_complete,
             'observed_at': iso(snapshot.observed_at)})
    return groups


class QuotaSensors:
    def __init__(self, client, device_id, root, prefix, home, zone):
        self.client, self.device_id, self.root = client, device_id, root
        self.prefix, self.home, self.zone = prefix, home, ZoneInfo(zone)
        self.configs = {}
        self.known = set()

    def reset_discovery(self):
        self.configs.clear()

    def discovery(self, key, name, field, label, kind):
        state_topic = f'{self.root}/quotas/{key}'
        config = {
            'name': f'{name} {label}', 'unique_id': f'{self.device_id}_{key}_{field}',
            'device': {'identifiers': [f'quota_strip_{self.device_id}'], 'name': 'Quota Strip'},
            'state_topic': state_topic, 'value_template': '{{ value_json.' + field + ' }}',
            'expire_after': 90, 'qos': 0,
            'availability': [{'topic': f'{self.root}/availability'},
                             {'topic': state_topic, 'value_template':
                              "{{ 'online' if value_json.available and value_json." + field +
                              " is not none else 'offline' }}"}],
            'availability_mode': 'all',
            'json_attributes_topic': state_topic,
            'json_attributes_template': "{{ {'observed_at': value_json.observed_at} | tojson }}",
        }
        if kind == 'timestamp':
            config['device_class'] = 'timestamp'
            if field == 'expiry':
                config['json_attributes_template'] = (
                    "{{ {'observed_at': value_json.observed_at, 'details_complete': "
                    "value_json.expiry_details_complete} | tojson }}")
        else:
            config.update(state_class='measurement', suggested_display_precision=0)
            if kind == '%':
                config['unit_of_measurement'] = '%'
                config['icon'] = 'mdi:gauge'
            else:
                config['icon'] = 'mdi:restore'
        return config

    def refresh(self, now):
        if not self.client.is_connected():
            return
        current = {}
        for provider in ('claude', 'codex'):
            try:
                snapshot = Snapshot.from_dict(json.loads(
                    (self.home / f'{provider}-snapshot.json').read_text()))
                if snapshot.provider != provider:
                    continue
                current.update(sensor_groups(snapshot, now, self.zone))
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                continue
        for key, (name, fields, values) in current.items():
            for field, (label, kind) in fields.items():
                topic = f'{self.prefix}/sensor/quota_strip_{self.device_id}/{key}_{field}/config'
                config = json.dumps(self.discovery(key, name, field, label, kind), sort_keys=True)
                if self.configs.get(topic) != config:
                    info = self.client.publish(topic, config, qos=1, retain=True)
                    if info.rc == 0:
                        self.configs[topic] = config
            # Non-retained state plus expire_after prevents replaying old values as fresh.
            self.client.publish(f'{self.root}/quotas/{key}', json.dumps(values), qos=0, retain=False)
        for key in self.known - current.keys():
            self.client.publish(f'{self.root}/quotas/{key}', json.dumps({
                'available': False, 'observed_at': None, 'used': None, 'remaining': None,
                'reset': None, 'budget': None, 'count': None, 'expiry': None,
                'expiry_details_complete': False}), qos=0, retain=False)
        self.known.update(current)
