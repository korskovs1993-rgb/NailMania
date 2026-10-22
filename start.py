"""Double-click friendly, loopback-only demo launcher for Windows.

No service installation or system settings changes. Production uses README.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


def run(*args):
    subprocess.run([str(PYTHON), *args], cwd=ROOT, check=True)


def env_values():
    values = {}
    for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return values


if __name__ == '__main__':
    os.chdir(ROOT)
    PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    try:
        if not PYTHON.exists():
            print('Creating the local NeilMania environment...')
            venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
        # Fast, idempotent; also repairs an interrupted dependency installation.
        run('-m', 'pip', 'install', '-r', 'requirements.txt', '--disable-pip-version-check')
        if not (ROOT / '.env').exists():
            run('init_local.py')
        values = env_values()
        if os.environ.get('DEBUG', values.get('DEBUG')) != '1' or os.environ.get('DEMO_SQLITE', values.get('DEMO_SQLITE')) != '1':
            raise RuntimeError('This launcher is for the local SQLite demo only. Use README for PostgreSQL/production.')
        run('manage.py', 'migrate', '--noinput')
        run('manage.py', 'collectstatic', '--noinput')
        run('manage.py', 'setup_studio')
        run('manage.py', 'seed_demo')
        port = None
        for candidate in range(8000, 8010):
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', candidate))
                    port = candidate
                    break
                except OSError:
                    continue
        if port is None:
            raise RuntimeError('Ports 8000-8009 are occupied. Close an old demo server first.')
        url = f'http://127.0.0.1:{port}/studio/'
        # Override only for this loopback demo process; .env remains unchanged.
        child_env = dict(os.environ, PUBLIC_BOOKING_ENABLED='1')
        server = subprocess.Popen([str(PYTHON), '-m', 'waitress',
                                   f'--listen=127.0.0.1:{port}', '--threads=1',
                                   'nailbook.wsgi:application'], cwd=ROOT, env=child_env)
        notifications = None
        try:
            notifications = subprocess.Popen([str(PYTHON), 'manage.py', 'send_notifications', '--loop'], cwd=ROOT, env=child_env)
            for _ in range(30):
                if server.poll() is not None:
                    raise RuntimeError('The local server failed to start.')
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.5)
            else:
                raise RuntimeError('The local server did not respond in time.')
            print(f'\nNeilMania: {url}\nDemo login: demo\nDemo password: NailBook-demo-2026!')
            print('Use fictional data only. Keep this window open. Ctrl+C stops the demo.')
            webbrowser.open(url)
            server.wait()
        except KeyboardInterrupt:
            pass
        finally:
            if notifications and notifications.poll() is None:
                notifications.terminate()
                notifications.wait(timeout=10)
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=10)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'\nNeilMania could not start: {error}')
        input('Press Enter to close...')
        raise SystemExit(1)
