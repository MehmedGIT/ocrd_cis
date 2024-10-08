from __future__ import absolute_import
import os

import click
import json

from ocrd import Processor, Workspace
from ocrd.decorators import ocrd_cli_options, ocrd_cli_wrap_processor
from ocrd_utils import getLevelName, pushd_popd
from ocrd_cis import JavaPostCorrector


@click.command()
@ocrd_cli_options
def ocrd_cis_postcorrect(*args, **kwargs):
    return ocrd_cli_wrap_processor(PostCorrector, *args, **kwargs)

class PostCorrector(Processor):
    @property
    def executable(self):
        return 'ocrd-cis-postcorrect'

    def setup(self):
        # since ocrd v3.0 we cannot overwrite self.parameter anymore
        # because that gets validated against the schema
        # (so these additions would fail)
        self.params = dict(self.parameter)
        profiler = {}
        profiler["path"] = self.parameter["profilerPath"]
        profiler["config"] = self.parameter["profilerConfig"]
        profiler["noCache"] = True
        self.params["profiler"] = profiler
        self.params["runDM"] = True
        self.logger.debug(json.dumps(self.params, indent=4))

    def process_workspace(self, workspace: Workspace):
        with pushd_popd(workspace.directory):
            self.workspace = workspace
            self.verify()
            # this CLI call mimics the OCR-D processor CLI itself
            # we have no control over its interior
            # (we get no page-wise error handling and input downloading)
            p = JavaPostCorrector(self.workspace.mets_target,
                                  self.input_file_grp,
                                  self.output_file_grp,
                                  self.params,
                                  getLevelName(self.logger.getEffectiveLevel()))
            p.exe()
            # reload the mets file to prevent run_processor's save_mets
            # from overriding the results from the Java process
            self.workspace.reload_mets()
            # workaround for cisocrgroup/ocrd-postcorrection#13 (absolute paths in output):
            for output_file in self.workspace.find_files(file_grp=self.output_file_grp):
                flocat = output_file._el.find('{http://www.loc.gov/METS/}FLocat')
                flocat.attrib['LOCTYPE'] = 'OTHER'
                flocat.attrib['OTHERLOCTYPE'] = 'FILE'
                output_file.local_filename = os.path.relpath(output_file.local_filename, self.workspace.directory)
