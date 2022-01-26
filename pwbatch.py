#!/usr/bin/env python

from __future__ import print_function
from __future__ import unicode_literals

import argparse
import os
import re
import sys

from functools import partial

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

def pw_list_patches(rpc, project, state):
    filt = patches.Filter()
    filt.add('project', project)
    filt.add('state', state)
    filt.add('archived', False)
    filt.resolve_ids(rpc)

    ps = rpc.patch_list(filt.d)
    for p in ps:
        yield p

def pwbatch(project_alias, current_state, state_func):
    # grab settings from config files
    config = utils.configparser.ConfigParser()
    config.read([CONFIG_FILE])

    try:
        project = config.get('options', project_alias)
    except (utils.configparser.NoSectionError,
            utils.configparser.NoOptionError):
        sys.stderr.write(
            'No default project configured in %s\n' % CONFIG_FILE)
        sys.exit(1)

    url = config.get(project, 'url')

    transport = xmlrpc.Transport(url)
    transport.set_credentials(config.get(project, 'username'),
                              config.get(project, 'password'))

    try:
        rpc = xmlrpc.xmlrpclib.Server(url, transport=transport)
    except (IOError, OSError):
        sys.stderr.write("Unable to connect to %s\n" % url)
        sys.exit(1)

    all_states = list_states(rpc)
    patches = pw_list_patches(rpc, project, current_state)

    with open('/tmp/pwbatch', 'w') as f:
        for state in all_states:
            f.write('# %s\n' % state)

        for patch in patches:

            if state_func:
                state = state_func(rpc, patch)
            else:
                state = patch['state']

            f.write('[%s] %d %s\n' % (state, patch['id'], patch['name']))

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

                if state_str == current_state:
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

def git_refspec_to_msgids(refspec):
    with Popen(['git', 'rev-list', refspec], stdout=PIPE) as rev_list:
        commits = [commit.decode('utf-8').strip() for commit in rev_list.stdout.readlines()]

        for commit in commits:
            with Popen(['git', 'cat-file', 'commit', commit], stdout=PIPE) as cat_file:
                msg = cat_file.stdout.read().decode('utf-8')

                m = re.search('Link: https://lore.kernel.org/r/(.+)', msg)
                if not m:
                    continue

                msg_id = m.group(1)
                yield msg_id

def is_accepted(msgids, rpc, patch):
        msgid = str(patch["msgid"]).strip("<>")

        if msgid in msgids:
            return 'Accepted'

        return patch['state']

def is_applicable(rpc, patch):
    matches = [
        'Documentation/devicetree/bindings/arm/msm/',
        'Documentation/devicetree/bindings/arm/qcom.yaml',
        'Documentation/devicetree/bindings/clock/qcom,',
        'Documentation/devicetree/bindings/soc/qcom/',
        'Documentation/devicetree/bindings/firmware/qcom',
        'Documentation/devicetree/bindings/reserved-memory/qcom',
        'MAINTAINERS',
        'arch/arm/boot/dts/qcom-',
        'arch/arm/configs/multi_v7_defconfig',
        'arch/arm/configs/qcom_defconfig',
        'arch/arm/mach-qcom/',
        'arch/arm64/boot/dts/qcom/',
        'arch/arm64/configs/defconfig',
        'drivers/clk/qcom/',
        'drivers/firmware/qcom_',
        'drivers/soc/qcom/',
        'include/dt-bindings/clock/qcom',
        'include/linux/qcom_scm.h',
        'include/linux/soc/qcom/',
        'include/soc/qcom/',
    ]

    mbox = rpc.patch_get_mbox(patch['id'])
    with Popen(['lsdiff', '--strip=1'], stdin=PIPE, stdout=PIPE) as lsdiff:
        lsdiff.stdin.write(mbox.encode('utf-8'))
        lsdiff.stdin.close()

        files = [line.decode('utf-8').strip() for line in lsdiff.stdout.readlines()]
        for file in files:
            for match in matches:
                if file.startswith(match):
                    return patch['state']

    return 'Not Applicable'

def main():
    parser = argparse.ArgumentParser(description='patchwork batch updater')
    parser.add_argument('-p', '--project', default='default')
    parser.add_argument('--mark-accepted', metavar='<refspec>')
    parser.add_argument('--not-applicable', action='store_true')

    args = parser.parse_args()

    state_func = None
    if args.mark_accepted:
        current_state = 'Queued'
        state_func = partial(is_accepted, list(git_refspec_to_msgids(args.mark_accepted)))
    elif args.not_applicable:
        current_state = 'New'
        state_func = partial(is_applicable)
    else:
        current_state = 'New'

    pwbatch(project_alias=args.project, current_state=current_state, state_func=state_func)

if __name__ == "__main__":
    try:
        main()
    except (UnicodeEncodeError, UnicodeDecodeError):
        import traceback
        traceback.print_exc()
        sys.stderr.write('Try exporting the LANG or LC_ALL env vars. See '
                         'pwclient --help for more details.\n')
        sys.exit(1)
