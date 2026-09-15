import bpy, pbr2gta_audit as s
s.prefs.nvcompress_path=s.saved_prefs['nvcompress_path']
s.prefs.nvtt_sha256=''
result={'returned':sorted(bpy.ops.pbr2gta.validate_nvtt('EXEC_DEFAULT')),
        'error':s.prefs.nvtt_error,'sha256':s.prefs.nvtt_sha256}
