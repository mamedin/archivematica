#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# This file is part of Archivematica.
#
# Copyright 2010-2017 Artefactual Systems Inc. <http://artefactual.com>
#
# Archivematica is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Archivematica is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Archivematica.  If not, see <http://www.gnu.org/licenses/>.

"""Bind a PID to the input ``SIP`` model and possibly also to all of the
``Directory`` models corresponding to all of the subdirectories within the SIP.

Binding a PID means making a single request to a Handle web API endpoint to:

1. request that the SIP's Transfer's accession number (or Directory's UUID) be
   registered as a handle (i.e., persistent identifier or PID), and
2. configure that PID/UUID-as-URL, i.e., PURL, to resolve to a base "resolve"
   URL and possibly also configure a number of qualified PURLs to resolve to
   related "resolve" URLs.

The idea is to allow for PURL resolution like:

    http://<PID-RESOLVER>/<NAME_AUTH>/<PID>
        => http://my-org-domain.org/dip/<PID>
    http://<PID-RESOLVER>/<NAME_AUTH>/<PID>?locatt=view:mets
        => http://my-org-domain.org/mets/<PID>

The sole command-line argument is the SIP's UUID. If the --pind-pids option
is something other than 'Yes', the script will exit without doing anything.
"""

from __future__ import print_function

import argparse
from functools import wraps
from itertools import chain
import sys

import django
django.setup()
# dashboard
from main.models import DashboardSetting, Directory, Identifier, SIP
# archivematicaCommon
from archivematicaFunctions import str2bool
from bindpid import bind_pid, BindPIDException
from custom_handlers import get_script_logger


logger = get_script_logger('archivematica.mcp.client.bind_pids')


class BindPIDsException(Exception):
    """If I am raised, return 1."""
    exit_code = 1


class BindPIDsWarning(Exception):
    """If I am raised, return 0."""
    exit_code = 0


def exit_on_known_exception(func):
    """Decorator that makes this module's ``main`` function cleaner by handling
    early exiting by catching particular exceptions.
    """
    @wraps(func)
    def wrapped(*_args, **kwargs):
        try:
            func(*_args, **kwargs)
        except (BindPIDsException, BindPIDsWarning) as exc:
            return exc.exit_code
    return wrapped


def _exit_if_not_bind_pids(bind_pids_switch):
    """Quit processing if bind_pids_switch is not truthy."""
    if not bind_pids_switch:
        logger.info('Configuration indicates that PIDs should not be bound.')
        raise BindPIDsWarning


def _add_pid_to_mdl_identifiers(mdl, config):
    """Add the newly minted handle to the ``SIP`` or ``Directory`` model as an
    identifier in its m2m ``identifiers`` attribute.
    """
    identifier = Identifier.objects.create(
        type='hdl:{}'.format(config['naming_authority']),
        value='{}/{}'.format(config['naming_authority'], config['desired_pid']))
    mdl.identifiers.add(identifier)


def _get_sip(sip_uuid):
    try:
        return SIP.objects.get(uuid=sip_uuid)
    except SIP.DoesNotExist:
        raise BindPIDsException


def _bind_pid_to_model(mdl, config):
    entity_type = 'unit' if isinstance(mdl, SIP) else 'file'
    config.update({'entity_type': entity_type,
                   'desired_pid': mdl.uuid})
    try:
        msg = bind_pid(**config)
        _add_pid_to_mdl_identifiers(mdl, config)
        print(msg)  # gets appended to handles.log file, cf. StandardTaskConfig
        logger.info(msg)
        return 0
    except BindPIDException as exc:
        print(exc, file=sys.stderr)
        logger.info(exc)
        raise BindPIDsException


@exit_on_known_exception
def main(sip_uuid, bind_pids_switch):
    """Bind the UUID ``sip_uuid`` to the appropriate URL(s), given the
    configuration in the dashboard, Do this only if ``bind_pids_switch`` is
    ``True``.
    """
    _exit_if_not_bind_pids(bind_pids_switch)
    for mdl in chain([_get_sip(sip_uuid)],
                     Directory.objects.filter(sip_id=sip_uuid).all()):
        _bind_pid_to_model(mdl, DashboardSetting.objects.get_dict('handle'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('sip_uuid', type=str,
                        help='The UUID of the SIP to bind a PID for; any'
                             ' directories associated to this SIP will have'
                             ' PIDs bound as well.')
    parser.add_argument('--bind-pids', action='store', type=str2bool,
                        dest="bind_pids_switch", default='No')
    args = parser.parse_args()
    if args.sip_uuid == 'None':
        sys.exit(0)
    logger.info('bind_pids called with args: %s', vars(args))
    sys.exit(main(**vars(args)))
