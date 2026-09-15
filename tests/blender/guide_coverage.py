import sys,importlib
s=sys.modules['pbr2gta_audit']
ui=importlib.import_module('bl_ext.user_default.pbr2gta.shader_guide_ui')
slots=importlib.import_module('bl_ext.user_default.pbr2gta.shader_slots')
class Layout:
    def __init__(self,rows):self.rows=rows
    def label(self,*args,**kwargs):self.rows.append(kwargs.get('text',''))
    def __getattr__(self,name):return lambda *args,**kwargs:self
errors=[]
missing=[]
counts=[]
for name in slots.manifest()['shaders']:
    try:
        resolved=ui.get_resolved_shader_guide(name)
        for lang in ('RU','EN'):
            for section in ('MAPS','MESH','VALUES'):
                rows=[]
                ui.draw_full_shader_guide(Layout(rows),resolved,section,True,True,language=lang)
                counts.append({'shader':name,'lang':lang,'section':section,'lines':len(rows)})
                if any('Description unavailable for this shader variant.' in row for row in rows):
                    missing.append({'shader':name,'lang':lang,'section':section})
    except Exception as exc:
        errors.append({'shader':name,'error':repr(exc)})
result=s.record('guide_coverage_verified',{'shaders':len(slots.manifest()['shaders']),'layouts':len(counts),'errors':errors,'missing_translations':missing,'longest':sorted(counts,key=lambda i:i['lines'],reverse=True)[:8]})
