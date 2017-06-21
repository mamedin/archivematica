#!/usr/bin/env python2

from __future__ import print_function
import os
import sys
import uuid

import django
django.setup()
# dashboard
from fpr.models import FPCommand
from main.models import FileFormatVersion, File, Transfer

# archivematicaCommon
from custom_handlers import get_script_logger
from executeOrRunSubProcess import executeOrRun
from databaseFunctions import fileWasRemoved, create_directory_models
from fileOperations import addFileToTransfer, updateSizeAndChecksum
from archivematicaFunctions import get_dir_uuids, format_subdir_path

# clientScripts
from hasPackages import already_extracted

file_path_cache = {}


logger = get_script_logger("archivematica.mcp.client.extractContents")


def output_directory(file_path, date):
    if file_path_cache.get(file_path):
        return file_path_cache[file_path]
    else:
        path = file_path + '-' + date
        file_path_cache[file_path] = path
        return path


def tree(root):
    for dirpath, __, files in os.walk(root):
        for file in files:
            yield os.path.join(dirpath, file)


def assign_uuid(filename, package_uuid, transfer_uuid, date, task_uuid, sip_directory, package_filename):
    file_uuid = uuid.uuid4().__str__()
    relative_path = filename.replace(sip_directory, "%transferDirectory%", 1)
    relative_package_path = package_filename.replace(sip_directory, "%transferDirectory%", 1)
    package_detail = "{} ({})".format(relative_package_path, package_uuid)
    event_detail = "Unpacked from: " + package_detail
    addFileToTransfer(relative_path, file_uuid, transfer_uuid, task_uuid, date,
                      sourceType="unpacking", eventDetail=event_detail)
    updateSizeAndChecksum(file_uuid, filename, date, uuid.uuid4().__str__())

    print('Assigning new file UUID:', file_uuid, 'to file', filename)


def _get_subdir_paths(root_path, path_prefix_to_repl):
    """Return a generator of subdirectory paths in ``root_path`` with
    the ancestor path ``path_prefix_to_repl`` replaced by a placeholder
    string.
    """
    return (format_subdir_path(dir_path, path_prefix_to_repl) for
            dir_path, __, ___ in os.walk(root_path))


def delete_and_record_package_file(file_path, file_uuid, current_location):
    os.remove(file_path)
    print("Package removed: " + file_path)
    event_detail_note = "removed from: " + current_location
    fileWasRemoved(file_uuid, eventDetail=event_detail_note)


def main(transfer_uuid, sip_directory, date, task_uuid, delete=False):
    files = File.objects.filter(transfer=transfer_uuid, removedtime__isnull=True)
    if not files:
        print('No files found for transfer: ', transfer_uuid)

    transfer_mdl = Transfer.objects.get(uuid=transfer_uuid)

    # We track whether or not anything was extracted because that controls what
    # the next microservice chain link will be.
    # If something was extracted, then a new identification step has to be
    # kicked off on those files; otherwise, we can go ahead with the transfer.
    extracted = False

    for file_ in files:
        try:
            format_id = FileFormatVersion.objects.get(file_uuid=file_.uuid)
        # Can't do anything if the file wasn't identified in the previous step
        except:
            print('Not extracting contents from',
                  os.path.basename(file_.currentlocation),
                  ' - file format not identified',
                  file=sys.stderr)
            continue
        if format_id.format_version is None:
            print('Not extracting contents from',
                  os.path.basename(file_.currentlocation),
                  ' - file format not identified',
                  file=sys.stderr)
            continue
        # Extraction commands are defined in the FPR just like normalization
        # commands
        try:
            command = FPCommand.active.get(
                fprule__format=format_id.format_version,
                fprule__purpose='extract',
                fprule__enabled=True,
            )
        except FPCommand.DoesNotExist:
            print('Not extracting contents from',
                  os.path.basename(file_.currentlocation),
                  ' - No rule found to extract',
                  file=sys.stderr)
            continue

        # Check if file has already been extracted
        if already_extracted(file_):
            print('Not extracting contents from',
                  os.path.basename(file_.currentlocation),
                  ' - extraction already happened.',
                  file=sys.stderr)
            continue

        file_path = file_.currentlocation.replace('%transferDirectory%', sip_directory)

        if command.script_type == 'command' or command.script_type == 'bashScript':
            args = []
            command_to_execute = command.command.replace('%inputFile%',
                                                         file_path)
            command_to_execute = command_to_execute.replace('%outputDirectory%',
                                                            output_directory(file_path, date))
        else:
            command_to_execute = command.command
            args = [file_path, output_directory(file_path, date)]

        exitstatus, stdout, stderr = executeOrRun(command.script_type,
                                                  command_to_execute,
                                                  arguments=args,
                                                  printing=True)

        if not exitstatus == 0:
            # Dang, looks like the extraction failed
            print('Command', command.description, 'failed!', file=sys.stderr)
        else:
            extracted = True
            print('Extracted contents from', os.path.basename(file_path))

            # Assign UUIDs and insert them into the database, so the newly-
            # extracted files are properly tracked by Archivematica
            extracted_path = output_directory(file_path, date)
            for extracted_file in tree(extracted_path):
                assign_uuid(extracted_file, file_.uuid, transfer_uuid, date,
                            task_uuid, sip_directory, file_.currentlocation)

            # Assign UUIDs to directories via ``Directory`` objects in the
            # database.
            if transfer_mdl.diruuids:
                create_directory_models(
                    get_dir_uuids(
                        _get_subdir_paths(extracted_path, sip_directory),
                        logger),
                    transfer_mdl)

            # We may want to remove the original package file after extracting its contents
            if delete:
                delete_and_record_package_file(file_path, file_.uuid, file_.currentlocation)

    if extracted:
        return 0
    else:
        return -1


if __name__ == '__main__':

    transfer_uuid = sys.argv[1]
    sip_directory = sys.argv[2]
    date = sys.argv[3]
    task_uuid = sys.argv[4]
    # Whether or not to remove the package file post-extraction
    # This is set by the user during the transfer, and defaults to false.
    delete = False
    if sys.argv[5] == "True":
        delete = True
    print("Deleting?: {}".format(delete), file=sys.stderr)
    sys.exit(main(transfer_uuid, sip_directory, date, task_uuid, delete=delete))
