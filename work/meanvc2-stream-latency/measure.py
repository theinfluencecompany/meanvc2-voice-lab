"""Tail flush helper matching the pinned upstream runtime."""
import numpy as np
import torch

def flush_like_upstream(vc):
    """Exact tail policy of upstream process_file, without file I/O in timing."""
    parts = []
    while vc.bn_buffer is not None and vc.bn_buffer.shape[1] >= vc.chunk_size:
        pad_cur = vc.bn_buffer[:, :vc.chunk_size, :]
        if vc.bn_buffer.shape[1] > vc.chunk_size:
            pad_fut = vc.bn_buffer[:, vc.chunk_size:vc._min_bn_len, :]
        else:
            pad_fut = vc.bn_buffer[:, -1:, :].repeat(1, vc.block_size, 1)
        cond = torch.cat([pad_cur, pad_fut], dim=1)
        vc.bn_buffer = vc.bn_buffer[:, vc.chunk_size:, :]
        if vc.bn_buffer.shape[1] == 0:
            vc.bn_buffer = None
        parts.append(vc._decode_mel(vc._vc_step(cond)))
    if vc.bn_buffer is not None and vc.bn_buffer.shape[1] > 0:
        n_rem = vc.bn_buffer.shape[1]
        last_frame = vc.bn_buffer[:, -1:, :]
        pad_cur = last_frame.repeat(1, vc.chunk_size, 1)
        pad_cur[:, :n_rem, :] = vc.bn_buffer
        cond = torch.cat([pad_cur, last_frame.repeat(1, vc.block_size, 1)], dim=1)
        vc.bn_buffer = None
        parts.append(vc._decode_mel(vc._vc_step(cond)))
    if vc.last_wav is not None:
        parts.append(vc.last_wav)
    return np.concatenate(parts) if parts else None
