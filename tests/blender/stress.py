import bpy,importlib,traceback
import pbr2gta_audit as s
cleanup=importlib.import_module(s.ops.__package__+'.image_cleanup')
original_flush=cleanup.flush
s.cleanup_observations=[]
def tracked_flush():
    job=bpy.app.is_job_running('RENDER_PREVIEW');before=len(cleanup._pending)
    answer=original_flush()
    after=len(cleanup._pending)
    s.cleanup_observations.append({'preview_job':job,'before':before,'after':after})
    if job:assert before==after
    return answer
cleanup.flush=tracked_flush

def sequence():
    counts=[]
    s.other.asset_mark()
    for index in range(15):
        s.other.asset_generate_preview()
        s.start('cycles_preview_'+str(index),roots=[s.other_model],preview=True)
        while bpy.context.window_manager.pbr2gta_running:yield .05
        # Force another native thumbnail render while retired images are queued.
        s.other.asset_generate_preview()
        while cleanup._pending or bpy.app.is_job_running('RENDER_PREVIEW'):yield .05
        counts.append(len([i for i in bpy.data.images if i.get('pbr2gta_generated') and i.users==0]))
    assert len(set(counts))==1,counts
    assert any(row['preview_job'] and row['before']>0 for row in s.cleanup_observations),s.cleanup_observations
    cleanup.flush=original_flush
    s.record('cycles_preview_stress_verified',{'passed':True,'cycles':15,'unused_images':counts,'cleanup_observations':s.cleanup_observations})
s.stress=sequence()
def tick():
    try:return next(s.stress)
    except StopIteration:return None
    except Exception:
        s.record('stress_failed',{'traceback':traceback.format_exc(),'observations':s.cleanup_observations});return None
bpy.app.timers.register(tick,first_interval=.1)
result={'started':True}
