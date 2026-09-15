from pbr2gta_blender.naming import allocate_names, clean_token


def test_material_names_not_sources_and_order_independent():
    entries = [(str(i), name, slot, suffix)
               for i, name in enumerate(['Chain', 'Chain.001'])
               for slot, suffix in [('DiffuseSampler', 'd'), ('SpecSampler', 's'), ('BumpSampler', 'n')]]
    result = allocate_names(entries)
    assert set(result.values()) == {f'{name}_{role}.dds' for name in ['chain', 'chain.001'] for role in 'dsn'}
    assert result == allocate_names(reversed(entries))


def test_collision_expands_uuid_and_respects_reserved_files():
    entries = [('abcdefgh1', 'Chain?', 'DiffuseSampler', 'd'),
               ('abcdefgh2', 'CHAIN!', 'DiffuseSampler', 'd')]
    result = allocate_names(entries, ['CHAIN_ABCDEFGH1_D.DDS'])
    assert len(set(result.values())) == 2
    assert all(name != 'chain_d.dds' for name in result.values())
    assert 'chain_abcdefgh1_d.dds' not in result.values()
    assert result == allocate_names(reversed(entries), ['chain_abcdefgh1_d.dds'])


def test_non_ascii_truncation_and_sampler_collisions():
    assert clean_token('..Chain / detail--.001 ') == 'chain_detail_001'
    assert clean_token('Chain.001') == 'chain.001'
    entries = [('a'*32, '\u0446\u0435\u043f\u044c', 'PaletteSampler', 'PaletteSampler'),
               ('b'*32, 'x'*90+'a', 'DiffuseSampler', 'd'),
               ('c'*32, 'x'*90+'b', 'DiffuseSampler', 'd'),
               ('d'*32, 'other', 'one?', 'one?'), ('d'*32, 'other', 'one!', 'one!')]
    result = allocate_names(entries)
    assert result[('a'*32,'PaletteSampler')] == 'material_aaaaaaaa_palettesampler.dds'
    assert len(set(result.values())) == len(entries)


def test_existing_own_name_is_repeatable_and_other_names_reserved():
    entries = [('abcd1234', 'Chain', 'DiffuseSampler', 'd')]
    assert allocate_names(entries) == allocate_names(entries)
    assert allocate_names(entries, ['chain_d.dds'])[('abcd1234','DiffuseSampler')] == 'chain_abcd1234_d.dds'
