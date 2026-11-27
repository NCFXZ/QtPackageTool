# Qt Package Tool - A PyQt6-based application
# Copyright (c) 2025 NCFXZ
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import _winapi
import ctypes
import msvcrt
import os
import subprocess
import time
from codecs import getincrementaldecoder
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

import pywintypes
import win32con
import win32event
import win32file
import win32job
import win32pipe
import win32process
import winerror
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


_create_job = ctypes.WinDLL("kernel32", use_last_error=True).CreateJobObjectW
_create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
_create_job.restype = wintypes.HANDLE
_EXIT_GRACE_SECONDS = 1.0


class BuildProcess(QObject):
    """Run one build tool and wait for every process it started."""

    output = pyqtSignal(str, bool)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self._handles = None
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    @property
    def running(self) -> bool:
        return self._handles is not None

    def start(
        self,
        program: Path,
        args: list[str],
        cwd: str,
        env: dict[str, str],
        *,
        encoding: str = "utf-8",
        cmdline: str | None = None,
    ) -> None:
        if self.running:
            raise RuntimeError("A build tool is already running.")

        with ExitStack() as handles, ExitStack() as launch:
            job = _create_job(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.callback(_winapi.CloseHandle, job)
            limits = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            limit_flags = limits["BasicLimitInformation"]
            limit_flags["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation, limits
            )

            devnull = launch.enter_context(open(os.devnull, "rb"))
            std_handles = [msvcrt.get_osfhandle(devnull.fileno())]
            streams = []
            for is_stderr in (False, True):
                reader, writer = win32pipe.CreatePipe(None, 65536)
                handles.callback(reader.Close)
                launch.callback(writer.Close)
                std_handles.append(int(writer))
                decoder = getincrementaldecoder(encoding)("replace")
                streams.append([reader, decoder, is_stderr, ""])

            for handle in std_handles:
                os.set_handle_inheritable(handle, True)
            startup = subprocess.STARTUPINFO(
                dwFlags=subprocess.STARTF_USESTDHANDLES,
                hStdInput=std_handles[0],
                hStdOutput=std_handles[1],
                hStdError=std_handles[2],
                lpAttributeList={"handle_list": std_handles},
            )

            # Keep the primary thread suspended until descendants are covered by the job.
            # CreateProcess also limits inherited handles to the three standard streams.
            process, thread, _, _ = _winapi.CreateProcess(
                str(program),
                cmdline if cmdline is not None else subprocess.list2cmdline([str(program), *args]),
                None,
                None,
                True,
                win32con.CREATE_SUSPENDED | _winapi.CREATE_NO_WINDOW,
                env,
                cwd,
                startup,
            )
            handles.callback(_winapi.CloseHandle, process)
            launch.callback(_winapi.CloseHandle, thread)
            try:
                win32job.AssignProcessToJobObject(job, process)
                win32process.ResumeThread(thread)
            except Exception:
                win32process.TerminateProcess(process, 1)
                raise

            self._process = process
            self._job = job
            self._streams = streams
            self._stop_requested = False
            self._terminating = False
            self._exited_at = None
            self._poll_failures = 0
            self._handles = handles.pop_all()

        self._timer.start()

    def terminate(self) -> None:
        if self.running:
            self._stop_requested = True

    def _forward_output(self, final: bool = False) -> None:
        for stream in self._streams:
            pipe, decoder, is_stderr, pending = stream
            chunks = []
            try:
                while True:
                    _, available, _ = win32pipe.PeekNamedPipe(pipe, 0)
                    if not available:
                        break
                    _, chunk = win32file.ReadFile(pipe, available)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except pywintypes.error as exc:
                if exc.winerror not in (
                    winerror.ERROR_BROKEN_PIPE,
                    winerror.ERROR_NO_DATA,
                    winerror.ERROR_PIPE_NOT_CONNECTED,
                ):
                    self.output.emit(f"Could not read build output: {exc}\n", True)
            text = pending + decoder.decode(b"".join(chunks), final=final)
            if not text:
                continue
            if final:
                stream[3] = ""
                self.output.emit(text, is_stderr)
            else:
                split = text.rfind("\n")
                if split >= 0:
                    stream[3] = text[split + 1:]
                    self.output.emit(text[:split + 1], is_stderr)
                else:
                    stream[3] = text

    @pyqtSlot()
    def _poll(self) -> None:
        if not self.running:
            return
        try:
            exit_code = self._completed_exit_code()
            if exit_code is not None:
                self._forward_output(final=True)
        except (OSError, pywintypes.error) as exc:
            self._poll_failures += 1
            if self._poll_failures == 4:
                self._complete(error=f"Could not confirm build process exit: {exc}")
            return
        self._poll_failures = 0
        if exit_code is not None:
            self._complete(exit_code)

    def _completed_exit_code(self) -> int | None:
        if self._stop_requested and not self._terminating:
            win32job.TerminateJobObject(self._job, 1)
            self._terminating = True

        self._forward_output()
        status = win32event.WaitForSingleObject(self._process, 0)
        if status == win32event.WAIT_TIMEOUT:
            return None
        if status != win32event.WAIT_OBJECT_0:
            raise OSError(f"Unexpected Windows process wait status {status}")

        exit_code = win32process.GetExitCodeProcess(self._process)
        accounting = win32job.QueryInformationJobObject(
            self._job, win32job.JobObjectBasicAccountingInformation
        )
        if accounting["ActiveProcesses"]:
            if self._exited_at is None:
                self._exited_at = time.monotonic()
            # Terminate immediately on failure, or after a brief grace period for
            # resident helpers (such as MSVC's mspdbsrv.exe) once the tool finishes.
            if exit_code or (time.monotonic() - self._exited_at) >= _EXIT_GRACE_SECONDS:
                self.terminate()
            return None
        return exit_code

    def _complete(self, exit_code: int | None = None, *, error: str = "") -> None:
        self._timer.stop()
        handles, self._handles = self._handles, None
        try:
            # Closing the last job handle terminates any remaining processes.
            handles.close()
        except (OSError, pywintypes.error) as exc:
            close_error = f"Could not close build process handles: {exc}"
            error = f"{error}\n{close_error}" if error else close_error
        if error:
            self.failed.emit(error)
        else:
            self.finished.emit(exit_code)
