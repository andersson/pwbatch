#!/usr/bin/env python

from __future__ import print_function
from __future__ import unicode_literals

import os
import re
import sys

from pwclient import checks
from pwclient import parser
from pwclient import patches
from pwclient import projects
from pwclient import states
from pwclient import utils
from pwclient import xmlrpc

import pprint
pprint = pprint.PrettyPrinter().pprint

from subprocess import Popen, PIPE

CONFIG_FILE = os.path.expanduser('~/.pwclientrc')

def list_states(rpc):
    states = rpc.state_list("", 0)
    return [state['name'] for state in states]

def list_new_patches(rpc, project_str):
    filt = patches.Filter()
#    filt.add('max_count', -1)
    filt.add('project', project_str)
    filt.add('state', 'New')
    filt.add('archived', False)
    filt.resolve_ids(rpc)

    ps = rpc.patch_list(filt.d)
    for p in ps:
        yield p


def main(project='default'):
    # grab settings from config files
    config = utils.configparser.ConfigParser()
    config.read([CONFIG_FILE])

    try:
        project_str = config.get('options', project)
    except (utils.configparser.NoSectionError,
            utils.configparser.NoOptionError):
        sys.stderr.write(
            'No default project configured in %s\n' % CONFIG_FILE)
        sys.exit(1)

    url = config.get(project_str, 'url')

    transport = xmlrpc.Transport(url)
    transport.set_credentials(config.get(project_str, 'username'),
                              config.get(project_str, 'password'))

    try:
        rpc = xmlrpc.xmlrpclib.Server(url, transport=transport)
    except (IOError, OSError):
        sys.stderr.write("Unable to connect to %s\n" % url)
        sys.exit(1)

    all_states = list_states(rpc)
    patches = list_new_patches(rpc, project_str)

    with open('/tmp/pwbatch', 'w') as f:
        for state in all_states:
            f.write('# %s\n' % state)

        for patch in patches:
            f.write('[%s] %d %s\n' % (patch['state'], patch['id'], patch['name']))

    err_line = None

    while True:
        updates = []

        cmd = ['vim', '/tmp/pwbatch']
        if err_line:
            cmd.append('+%d' % err_line)

        with Popen(cmd) as proc:
            retval = proc.wait()
            if retval != 0:
                raise Exception('Failed to invoke vim')

        err_line = None
        current_line = 0

        with open('/tmp/pwbatch', 'r') as f:
            lines = f.readlines()

            for line in lines:
                current_line = current_line + 1

                if len(line) == 0:
                    continue
                if line[0] == '#':
                    continue

                m = re.search(r'\[(.*?)\]\s*(\d+)', line)
                if m is None:
                    continue

                patch_id = int(m.group(2))
                state_str = m.group(1)

                if state_str == 'New':
                    continue

                params = {}
                params['state'] = states.state_id_by_name(rpc, m.group(1))

                if params['state'] == 0:
                    err_line = current_line

                updates.append((patch_id, params))

        if err_line is None:
            break

    for update in updates:
        patch_id, params = update

        print("Updating %d" % patch_id)

        success = False
        try:
            success = rpc.patch_set(patch_id, params)
        except xmlrpclib.Fault as f:
            sys.stderr.write("Error updating patch %d: %s\n" % (patch_id, f.faultString))

        if not success:
            sys.stderr.write("Patch %d not updated\n" % patch_id)

if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (UnicodeEncodeError, UnicodeDecodeError):
        import traceback
        traceback.print_exc()
        sys.stderr.write('Try exporting the LANG or LC_ALL env vars. See '
                         'pwclient --help for more details.\n')
        sys.exit(1)
