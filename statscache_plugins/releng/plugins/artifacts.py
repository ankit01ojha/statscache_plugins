from __future__ import absolute_import
import statscache.plugins

import datetime
import json
import requests


class Plugin(statscache.plugins.BasePlugin):
    name = "releng, artifacts"
    summary = "Build logs for successful upload/test of cloud images"
    description = """
    Latest build logs for successful upload/test of cloud images
    """
    artifacts = ('appliance', 'livecd')

    def __init__(self, *args, **kwargs):
        super(Plugin, self).__init__(*args, **kwargs)
        self._seen = {}

    def handle(self, session, messages):
        rows = []
        for message in messages:
            if not (message['msg']['owner'] == 'masher' and
                    message['msg']['method'] in self.artifacts):
                continue
            artifact = message['msg']['method']
            srpm_name, srpm_link, arch = self.get_srpm_details(message['msg'])
            # FIXME: Have to include logic for overwriting older log
            name = srpm_name
            link = srpm_link if artifact == 'livecd' else None
            category = 'artifact-{}_{}'.format(artifact, arch)
            category_constraint = srpm_name
            msg_timestamp = datetime.datetime.fromtimestamp(
                message['timestamp'])
            last_seen_key = '{}:{}'.format(category, category_constraint)
            last_seen = self._seen.get(last_seen_key)
            if last_seen and msg_timestamp <= last_seen:
                continue
            self._seen[last_seen_key] = msg_timestamp
            msg = json.dumps({
                'name': name,
                'link': link,
                'status': message['msg']['new'],
                'extra_text': '(details)',
                'extra_link': message['meta']['link']
            })
            result = session.query(self.model)\
                .filter(self.model.category == category)\
                .filter(self.model.category_constraint == category_constraint)
            row = result.first()
            if row:
                row.timestamp = msg_timestamp
                row.message = msg
            else:
                row = self.model(
                    timestamp=msg_timestamp,
                    message=msg,
                    category=category,
                    category_constraint=category_constraint
                )
            rows.append(row)
        return rows

    def initialize(self, session, datagrepper_endpoint=None):
        latest = session.query(self.model).filter(
            self.model.category.startswith('artifact-')).order_by(
                self.model.timestamp.desc()).first()
        delta = 2000000
        if latest:
            delta = int(
                (datetime.datetime.now() - latest.timestamp).total_seconds())
        resp = requests.get(
            datagrepper_endpoint,
            params={
                'delta': delta,
                'rows_per_page': 100,
                'order': 'desc',
                'meta': 'link',
                'user': 'masher'
            }
        )
        rows = self.handle(session, datetime.datetime.now(),
                           resp.json().get('raw_messages', []))
        session.add_all(rows)
        session.commit()

    def get_srpm_details(self, msg):
        tokens = msg['srpm'].split('-')
        arch = tokens[-1]
        info = msg.get('info')
        srpm = msg['srpm']
        if isinstance(info, dict):
            options = info['request'][-1]
            if options.get('format'):
                srpm = '{} ({})'.format(
                    srpm, options['format'])
            branch = info['request'][1]
            if branch != 'rawhide':
                branch = 'branched'
            if branch:
                srpm = '{} ({})'.format(srpm, branch)
        # Try to generate SRPM link
        srpm_link = None
        children = info.get('children')
        if (msg['new'] == 'CLOSED' and isinstance(children, list) and
                len(children) == 1):
            id = children[0]['id']
            result = info['result'].split(' ')[-1]
            tokens = result.split('/')
            thing = tokens[5]
            r = info['request']
            opts = r[5]
            filename = '{}-{}-{}'.format(r[0], r[1], opts['release'])

            if opts.get('format') is None:
                filename += '.iso'
            elif opts['format'] == 'qcow2':
                filename += '-sda.qcow2'
            elif opts['format'] == 'raw':
                filename += '-sda.raw.xz'
            base = "https://kojipkgs.fedoraproject.org/work/tasks/"
            srpm_link = '{}{}/{}/{}'.format(base, thing, id, filename)
        return srpm, srpm_link, arch
