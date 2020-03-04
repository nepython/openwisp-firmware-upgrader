import os
import socket
from hashlib import sha256
from time import sleep

from billiard import Process
from paramiko.ssh_exception import NoValidConnectionsError

from openwisp_controller.connection.connectors.openwrt.ssh import OpenWrt as BaseOpenWrt


class AbortedUpgrade(Exception):
    pass


class OpenWrt(BaseOpenWrt):
    CHECKSUM_FILE = '/etc/openwisp/firmware_checksum'
    REMOTE_UPLOAD_DIR = '/tmp'
    UPGRADE_TIMEOUT = 70
    SLEEP_TIME = 20
    RETRY_TIME = 5
    log_lines = None

    def __init__(self, *args, **kwargs):
        self.log_lines = []
        super(OpenWrt, self).__init__(*args, **kwargs)

    def log(self, value):
        print(f'# {value}')
        self.log_lines.append(value)

    def upgrade(self, image):
        self.connect()
        checksum = sha256(image.read()).hexdigest()
        image.seek(0)
        # avoid upgrading if upgrade has already been performed previously
        try:
            self._compare_checksum(image, checksum)
        except AbortedUpgrade as e:
            self.disconnect()
            raise e
        remote_path = os.path.join(self.REMOTE_UPLOAD_DIR, image.name)
        self.upload(image.file, remote_path)
        self._test_image(remote_path)
        self.disconnect()
        self._reflash(remote_path)
        return self._write_checksum(checksum)

    def _compare_checksum(self, image, checksum):
        output, exit_code = self.exec_command(
            f'test -f {self.CHECKSUM_FILE}', exit_codes=[0, 1]
        )
        if exit_code == 0:
            self.log('Image checksum file found')
            cat = f'cat {self.CHECKSUM_FILE}'
            output, code = self.exec_command(cat)
            if checksum == output:
                message = 'Firmware already upgraded previously. ' \
                          'Identical checksum found in the filesystem, ' \
                          'no need to upgrade, aborting operation...'
                self.log(message)
                raise AbortedUpgrade(message)
            else:
                self.log('Checksum different, proceeding with '
                         'the upload of the new image...')
        else:
            self.log('Image checksum file not found, proceeding '
                     'with the upload of the new image...')

    def _test_image(self, path):
        self.exec_command(f'sysupgrade --test {path}')
        self.log('Sysupgrade test passed successfully, '
                 'proceeding with the upgrade operation...')

    def _reflash(self, path):
        """
        this will execute the upgrade operation in another process
        because the SSH connection may hang indefinitely while reflashing
        and would block the program; setting a timeout to `exec_command`
        doesn't seem to take effect so at least we can stop the process
        using `subprocess.join(timeout=self.UPGRADE_TIMEOUT)`
        """
        def upgrade(conn, path, timeout):
            conn.connect()
            conn.exec_command(f'sysupgrade -v -c {path}',
                              timeout=timeout)
            conn.close()
        subprocess = Process(
            target=upgrade,
            args=[self, path, self.UPGRADE_TIMEOUT]
        )
        subprocess.start()
        self.log('Upgrade operation in progress...')
        subprocess.join(timeout=self.UPGRADE_TIMEOUT)
        self.log(f'SSH connection closed, will wait {self.SLEEP_TIME} seconds before '
                 'attempting to reconnect...')
        sleep(self.SLEEP_TIME)
        # kill the subprocess if it has hanged
        if subprocess.is_alive():
            subprocess.terminate()
            subprocess.join()

    def _write_checksum(self, checksum):
        # 10 attempts
        for attempt in range(1, 11):
            self.log('Trying to reconnect to device '
                     f'(attempt n.{attempt})...')
            try:
                self.connect()
            except (NoValidConnectionsError, socket.timeout):
                self.log('Device not reachable yet, '
                         f'retrying in {self.RETRY_TIME} seconds...')
                sleep(self.RETRY_TIME)
                continue
            self.log('Connected! Writing checksum '
                     f'file to {self.CHECKSUM_FILE}')
            checksum_dir = os.path.dirname(self.CHECKSUM_FILE)
            self.exec_command(f'mkdir -p {checksum_dir}')
            self.disconnect()
            return True
            self.log('Upgrade completed successfully.')
            break
        else:
            self.log('Giving up, device not reachable '
                     'anymore after upgrade')
            return False
