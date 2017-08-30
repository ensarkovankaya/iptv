import subprocess
import threading
import time
from logging import getLogger

from django.utils import timezone

from recorder.models import Record as RecodModel

logger = getLogger('recorder.record')


class Recorder(threading.Thread):
    def __init__(self, id, wait=False, sleep=2):
        threading.Thread.__init__(self, daemon=True)
        logger.debug("Recorder Initialized with id: %s" % id)
        self.id = id
        self.sleep = sleep
        self.wait = wait
        self.ps = None
        self.terminated = False
        self.completed = False
        self.rcd = self.get_object(id)

    def get_object(self, id):
        try:
            return RecodModel.objects.get(id=id)
        except RecodModel.DoesNotExist as err:
            logger.error("Record object not found with id %s" % id)
            raise err
        except Exception as err:
            logger.error("Record object can not get with id %s" % id)
            raise err

    def terminate_process(self):
        try:
            if not self.ps:
                logger.warning("Process is None")
                return

            logger.warning("Process terminated.")
            #  If canceled terminate process
            self.ps.terminate()
            time.sleep(.5)  # wait a half second
            if not self.ps.returncode:  # If not terminated force to kill
                logger.debug("Process could not terminated trying to kill")
                self.ps.kill()
            self.terminated = True
        except Exception:
            logger.exception("Terminating process failed.")
            raise

    def is_process_running(self):
        return (self.ps.poll() is None) if self.ps else False

    def stop(self):
        if not self.is_process_running():
            logger.debug("Process is not running.")
            return

        try:
            self.terminate_process()
            self.mark_as_terminated()
        except Exception as err:
            logger.exception("Record could not stopped.")
            raise err

    def mark_as_terminated(self):
        try:
            self.rcd.status = 4
            self.rcd.save()
            self.rcd.add_log("Terminated")
        except Exception as err:
            logger.exception("Could not marked as terminated.")

    def mark_as_started(self):
        """Change record status as started and add record start time"""
        try:
            logger.debug("Mark record as started.")
            self.rcd.status = 1
            self.rcd.record_started = timezone.now()
            self.rcd.add_log("Mark Started")
            self.rcd.save()
        except Exception as err:
            logger.exception("Could not marked as started")
            raise err

    def mark_as_processing(self):
        """Change record status as processing and save pid """
        try:
            logger.debug("Mark record as processing.")
            self.rcd.status = 2
            self.rcd.pid = self.ps.pid
            self.rcd.add_log("Processing on pid: %s" % self.ps.pid)
            self.rcd.save()
        except Exception as err:
            logger.exception("Could not marked as processing.")
            raise err

    def mark_as_error(self, log=None):
        """Change record status as error and save add record end time"""
        try:
            logger.debug("Mark record as error.")
            self.rcd.status = 6
            self.rcd.record_ended = timezone.now()
            self.rcd.file.delete(save=False)
            self.rcd.save()
            if log:
                self.rcd.add_log(log)
        except Exception as err:
            logger.exception("Could not marked as error.")
            raise err

    def mark_as_completed(self):
        """Change record status as completed and add record end time"""
        try:
            logger.debug("Mark record as completed.")
            self.rcd.record_ended = timezone.now()
            self.rcd.status = 3
            self.rcd.save()
            self.rcd.add_log("Completed")
            self.completed = True
        except Exception as err:
            logger.exception("Could not marked as completed.")
            raise err

    def save_process_output(self):
        """Read process stderr output and saves it to the model logs."""
        try:
            logger.debug("Saving error logs")
            # Save Console Output
            self.rcd.add_log("".join([l.decode('utf-8') for l in self.ps.stderr.readlines()]))
        except Exception:
            logger.exception("Saving error logs failed.")
            pass

    def _loop(self):
        """This loop waits until process done or record terminated"""

        if not self.ps:
            logger.error("Loop method called but process not found.")
            raise ValueError("Process not found")

        check = 0
        # While process is running wait
        while self.is_process_running():
            # check if record canceled
            try:
                obj = self.get_object(self.id)
            except RecodModel.DoesNotExist:
                self.terminate_process()
                raise
            except Exception:
                raise

            if obj.terminate:
                self.stop()
                break

            check += 1
            logger.debug("Record: {id}, Pid: {pid}, Check: {check}".format(
                id=self.id, pid=self.ps.pid, check=check)
            )
            time.sleep(self.sleep)  # Wait

    def _start_process(self):
        try:
            cmd = self.rcd.generate_record_command()
            logger.debug("Command: %s" % cmd)
            self.ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.debug("Process started with pid: %s." % self.ps.pid)
        except Exception as err:
            logger.exception("Process could not started.")
            raise err

    def _wait(self):
        """Waits until Records start time pass."""
        while self.rcd.start_time > timezone.now():
            time.sleep(.5)

    def run(self):
        """!IMPORTANT: This method should not call directly, call 'start' method instead"""

        try:
            self.mark_as_started()
            self._start_process()  # Start Process

            if self.wait:
                self._wait()

            self.mark_as_processing()  # Mark Record as processing
            self._loop()  # Loop

            if self.terminated:
                return

            if self.ps.returncode != 0:  #  If not 0 means error
                logger.error("Record failed exit with %s" % self.ps.returncode)
                self.mark_as_error()
                self.save_process_output()
            else:
                #  Mark as Completed
                logger.debug("Completed")
                self.mark_as_completed()
        except Exception as err:
            logger.exception("Record failed.")
            try:
                self.mark_as_error(err)
            except:
                pass
            raise err