"""Fail-closed contract for the installed Sentence Transformers 6.x pooler."""

def require_mean_pooling(pooler):
    config = pooler.get_config_dict()
    def modes(value):
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return tuple(value)
        return ()
    if not isinstance(config, dict):
        raise ValueError('Unsupported pooling: configuration must be an object')
    # Inspect both serialized configuration and the attribute actually used by forward().
    if modes(config.get('pooling_mode')) != ('mean',) or modes(getattr(pooler, 'pooling_mode', None)) != ('mean',):
        raise ValueError('Unsupported pooling: explicit mean-only effective mode required')
    if any(k.startswith('pooling_mode_') for k in config):
        raise ValueError('Unsupported pooling: unconverted legacy or conflicting mode fields')
    if config.get('embedding_dimension') != 384 or getattr(pooler, 'embedding_dimension', None) != 384:
        raise ValueError('Unsupported pooling: expected 384 token dimensions')
    if config.get('include_prompt') is not True or getattr(pooler, 'include_prompt', None) is not True:
        raise ValueError('Unsupported pooling: prompt exclusion is not implemented')
    return 'mean'
