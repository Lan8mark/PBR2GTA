import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


def test_generated_images_wait_for_preview_job_and_recheck_users(monkeypatch):
    removed=[]
    class Image:
        users=0;use_fake_user=False;library=None
        def as_pointer(self):return id(self)
    released,preserved=Image(),Image()
    running={'preview':True}
    timers=SimpleNamespace(is_registered=lambda f:False, register=lambda *a,**k:None)
    fake=SimpleNamespace(app=SimpleNamespace(timers=timers,is_job_running=lambda j:running['preview']),
                         data=SimpleNamespace(screens=[],images=SimpleNamespace(remove=removed.append)))
    monkeypatch.setitem(sys.modules,'bpy',fake)
    path=Path(__file__).parents[1]/'src/pbr2gta_blender/image_cleanup.py'
    spec=importlib.util.spec_from_file_location('image_cleanup_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.queue_images([released,preserved])
    assert module.flush()==.25
    assert removed==[] and len(module._pending)==2
    preserved.users=1
    running['preview']=False
    assert module.flush() is None
    assert removed==[released]
    assert not module._pending
