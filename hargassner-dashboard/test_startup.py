"""Real startup credential/state boundaries with synthetic local dependencies."""
from contextlib import redirect_stdout
import io,json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import app_monitor as m
import run_app

class StartupTests(unittest.TestCase):
    def boot(self, *, token=True, failure=None, enabled=True):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); options=root/'options.json'
            options.write_text(json.dumps({'upstream_url':'https://dashboard.example:8443','ingress_token':'i'*43,'monitor_enabled':enabled,'monitor_token':'h'*64,'monitor_poll_interval':15}))
            settings=m.MonitorSettings.load(options); stopped=threading.Event(); output=io.StringIO(); created=[]
            class Server:
                def __init__(self,*args):
                    if failure=='ingress_listener':raise OSError('SECRET URL token=private')
                    created.append('listener')
                def serve_forever(self):stopped.wait(1)
                def shutdown(self):stopped.set()
                def server_close(self):created.append('closed')
            real_monitor=m.Monitor
            def construct(settings, credential):
                self.assertEqual(credential,'synthetic-only')
                if failure=='monitor_state':raise ValueError('SECRET private directory token')
                obj=real_monitor(settings,credential,root/'health-monitor',network=lambda _:self.fail('Network forbidden'))
                self.assertEqual((obj.directory.stat().st_mode & 0o777),0o700)
                created.append('monitor');return obj
            def install(_sig,cb):threading.Timer(.03,cb).start()
            env={'SUPERVISOR_TOKEN':'synthetic-only'} if token else {'HASSIO_TOKEN':'not-a-fallback'}
            with patch.dict(os.environ,env,clear=True), patch.object(run_app.MonitorSettings,'load',return_value=settings),patch.object(run_app,'Monitor',side_effect=construct),patch.object(run_app,'MonitorProxyServer',Server),patch.object(run_app.signal,'signal',side_effect=install),patch.object(m.Monitor,'run',lambda self,stop:stop.wait(1)),redirect_stdout(output):
                result=run_app.main()
            self.assertNotIn('SECRET',output.getvalue());self.assertNotIn('synthetic-only',output.getvalue())
            return result,output.getvalue(),created
    def test_real_env_and_private_state_enabled_boot(self):
        result,log,created=self.boot();self.assertEqual(result,0);self.assertEqual(created,['monitor','listener','closed']);self.assertIn('listening',log)
    def test_missing_env_is_prelistener_fixed_category(self):
        result,log,created=self.boot(token=False);self.assertEqual(result,1);self.assertEqual(created,[]);self.assertEqual(log,'App startup unavailable: supervisor_token.\n')
    def test_unsafe_state_fixed_category_and_no_listener(self):
        result,log,created=self.boot(failure='monitor_state');self.assertEqual(result,1);self.assertEqual(created,[]);self.assertEqual(log,'App startup unavailable: monitor_state.\n')
    def test_bind_failure_fixed_category(self):
        result,log,created=self.boot(failure='ingress_listener');self.assertEqual(result,1);self.assertEqual(created,['monitor']);self.assertEqual(log,'App startup unavailable: ingress_listener.\n')
    def test_disabled_no_token_and_no_state(self):
        result,log,created=self.boot(token=False,enabled=False);self.assertEqual(result,0);self.assertEqual(created,['listener','closed'])
    def test_docker_cmd_inherits_s6_environment(self):
        docker=(Path(__file__).parent/'Dockerfile').read_text();self.assertIn('CMD ["/command/with-contenv", "python3", "/app/run_app.py"]',docker);self.assertNotIn('S6_KEEP_ENV',docker)
if __name__=='__main__':unittest.main()
