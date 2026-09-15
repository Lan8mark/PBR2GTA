"""Read back existing matrix artifacts; does not launch or alter Blender."""
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def verify(folder):
    result = json.loads((folder/'result.json').read_text())
    assert result['passed']
    run = max(folder.glob('run-*'), key=lambda p:p.stat().st_mtime_ns)
    checked = {'xml_assets':0, 'native_assets':0, 'ytd_entries':0, 'ydd_assets':0}
    for path in run.rglob('*.xml'):
        if '.pbr2gta-txn-' in str(path):continue
        if not path.name.endswith(('.ydr.xml','.ydd.xml','.ytd.xml')):continue
        # Each export target is isolated; sources are found under its directory.
        available={p.stem.lower():p for p in path.parent.rglob('*.dds')}
        doc=ET.parse(path)
        if path.name.endswith('.ytd.xml'):
            for item in doc.findall('./Item'):
                name=item.findtext('Name')
                filename=item.findtext('FileName')
                assert filename and Path(filename).stem.lower()==name.lower()
                assert name.lower() in available,(path,name)
                checked['ytd_entries']+=1
            continue
        references={n.text.lower() for n in doc.findall('.//Parameters/Item/Name') if n.text and n.text!='None'}
        assert references<=available.keys(),(path,references-available.keys())
        if path.name.endswith('.ydd.xml'):
            assert {'audit_shared_d','audit_low_d'}<=references,(path,references)
            checked['ydd_assets']+=1
        checked['xml_assets']+=1
    natives=list(run.rglob('*.ydr'))+list(run.rglob('*.ydd'))
    if natives:
        deps=(folder/'deps_path.txt').read_text().strip()
        sys.path.insert(0,deps)
        from szio.gta5 import try_load_asset
        for path in natives:
            asset=try_load_asset(path)
            assert asset is not None,path
            drawables=list(asset.drawables.values()) if hasattr(asset,'drawables') else [asset]
            references={p.value.lower() for drawable in drawables for shader in drawable.shader_group.shaders
                        for p in shader.parameters if isinstance(p.value,str) and p.value and p.value!='None'}
            available={p.stem.lower() for p in path.parent.rglob('*.dds')}
            assert references<=available,(path,references-available)
            if path.suffix=='.ydd':assert {'audit_shared_d','audit_low_d'}<=references
            checked['native_assets']+=1
    assert checked['ydd_assets']>0
    (folder/'naming-readback.json').write_text(json.dumps(checked,indent=2))
    print(json.dumps(checked))


if __name__=='__main__':verify(Path(sys.argv[1]))
