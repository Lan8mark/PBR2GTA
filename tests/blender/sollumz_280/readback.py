import importlib
from szio.gta5 import try_load_asset
import pbr2gta_audit as s
rows=[]
for folder in ('verified_legacy','adapter_ydr','adapter_ydd'):
    for p in (s.root_path/folder).rglob('*'):
        if not p.name.endswith(('.ydr','.ydr.xml','.ydd','.ydd.xml')):continue
        asset=try_load_asset(p)
        drawables=list(asset.drawables.values()) if hasattr(asset,'drawables') else [asset]
        values=[]
        for drawable in drawables:
            values.extend(round(float(next(pa.value for pa in shader.parameters if pa.name.casefold()=='specularfresnel')[0]),2) for shader in drawable.shader_group.shaders)
        expected=[.79] if folder=='adapter_ydr' else [.37,.63,.37,.88]
        assert sorted(values)==sorted(expected),(str(p),values)
        rows.append({'file':str(p.relative_to(s.root_path)),'fresnel':values})
headers=[]
ytd=importlib.import_module(s.ops.__package__+'.ytdexport')
for p in (s.root_path/'adapter_ydd').rglob('*.dds'):
    header=ytd.parse_dds_header(p)
    assert header.width==512 and header.height==512 and header.mip_levels==8
    headers.append({'name':p.name,'format':header.format_name,'mips':header.mip_levels})
assert headers
result=s.record('readback',{'assets':rows,'dds':headers})
