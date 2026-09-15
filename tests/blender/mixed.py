import bpy, pbr2gta_audit as s
s.weapon=s.material('weapon',shader='weapon_normal_spec_palette.sps',surface=.46)
s.weapon_root=s.obj('weapon','sollumz_drawable')
s.weapon_model=s.model('weapon_model',s.weapon,s.weapon_root)
for role, suffix in [('diffuse','d'),('normal','n'),('specular','s')]:
    setattr(s.weapon.pbr2gta,role+'_name','audit_weapon_'+suffix)
for entry,definition,profile in s.bridge.material_slot_entries(s.weapon):
    if definition['transport_profile_id']=='PALETTE_RGBA8':
        entry.image=s.weapon.pbr2gta.base_color
        entry.output_name='audit_weapon_dpal'
s.direct=s.create_shader('default.sps');s.direct.name='audit_direct';s.materials.append(s.direct)
s.direct.pbr2gta.enabled=True
s.direct_root=s.obj('direct','sollumz_drawable');s.direct_model=s.model('direct_model',s.direct,s.direct_root)
for entry,definition,profile in s.bridge.material_slot_entries(s.direct):
    if definition['name']=='DiffuseSampler':
        entry.image=s.other.pbr2gta.base_color;entry.output_name='audit_direct_d'
result=s.start('mixed',roots=[s.weapon_model,s.direct_model],target_versions={'GEN8','GEN9'})
