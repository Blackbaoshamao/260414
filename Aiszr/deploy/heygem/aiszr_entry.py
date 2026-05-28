#!/usr/bin/env python
# coding=utf-8
"""Unified Docker entry: batch (:8383) + streaming inference endpoints.

Streaming uses HeyGem's own lmk blend path. The important quality detail is
that crop_lm must be a real 68-point PFLD landmark set; a five-point SCRFD
landmark approximation makes HeyGem's dynamic face mask degenerate.
"""
import os
os.chdir('/code')

import gc
import json
import multiprocessing
import threading
import time
import traceback
from enum import Enum

import cv2
import numpy as np
import torch
from flask import Flask, Response, request
from service.config import result_dir, server_ip, server_port, temp_dir
from service.self_logger import logger
from service.trans_dh_service import (Status, TransDhTask, a, get_aud_feat1,
                                       get_run_flag, init_p, init_wh,
                                       task_dic)

app = Flask(__name__)

_gpu_lock = threading.Lock()
_stream_sessions: dict = {}
_stream_session_seq = 0
_stream_lock = threading.Lock()

_global_model = None
_global_lm_model = None

# Dedicated audio feature extraction subprocess
_audio_feat_q_in: multiprocessing.Queue = None
_audio_feat_q_out: multiprocessing.Queue = None


def _load_model():
    global _global_model
    if _global_model is not None:
        return _global_model
    from landmark2face_wy.digitalhuman_interface import DigitalHumanModel, TestOptions
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.backends.cudnn.benchmark = True
    opt = TestOptions().parse()
    _global_model = DigitalHumanModel(opt, opt)
    _global_model.model.netG.half().eval()
    logger.info(f"DigitalHumanModel loaded: img_size={_global_model.img_size}")
    return _global_model


def _load_landmark_model():
    global _global_lm_model
    if _global_lm_model is not None:
        return _global_lm_model
    from face_detect_utils.face_detect import pfpld
    try:
        _global_lm_model = pfpld(False, 'face_detect_utils/resources')
    except Exception:
        logger.exception("PFLD CUDA landmark model load failed, retrying on CPU")
        _global_lm_model = pfpld(True, 'face_detect_utils/resources')
    logger.info("PFLD 68-point landmark model loaded")
    return _global_lm_model


# ======================================================================
# Batch endpoints — verbatim from app_local.py
# ======================================================================

class EasyResponse:
    def __init__(self, code, success, msg, data: dict):
        self.code = code
        self.success = success
        self.msg = msg
        self.data = data


class ResponseCode(Enum):
    system_error = [9999, '系统异常']
    success = [10000, '成功']
    busy = [10001, '忙碌中']
    error1 = [10002, '参数异常']
    error2 = [10003, '获取锁异常']
    error3 = [10004, '任务不存在']


@app.route('/easy/submit', methods=['POST'])
def easy_submit():
    request_data = json.loads(request.data)
    _code = request_data['code']
    if not get_run_flag():
        logger.warning('%s -> busy ', _code)
        return json.dumps(
            EasyResponse(ResponseCode.busy.value[0], True, ResponseCode.busy.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)

    try:
        if 'audio_url' not in request_data or request_data['audio_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'audio_url参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        if 'video_url' not in request_data or request_data['video_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'video_url参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        if 'code' not in request_data or request_data['code'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'code参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        _audio_url = request_data['audio_url']
        _video_url = request_data['video_url']
        _code = request_data['code']

        if 'watermark_switch' not in request_data or request_data['watermark_switch'] == '':
            _watermark_switch = 0
        else:
            _watermark_switch = 1 if str(request_data['watermark_switch']) == '1' else 0
        if 'digital_auth' not in request_data or request_data['digital_auth'] == '':
            _digital_auth = 0
        else:
            _digital_auth = 1 if str(request_data['digital_auth']) == '1' else 0
        if 'chaofen' not in request_data or request_data['chaofen'] == '':
            _chaofen = 0
        else:
            _chaofen = 1 if str(request_data['chaofen']) == '1' else 0
        if 'pn' not in request_data or request_data['pn'] == '':
            _pn = 1
        else:
            _pn = 1 if str(request_data['pn']) == '1' else 0
        task = TransDhTask(_code, _audio_url, _video_url, _watermark_switch, _digital_auth, _chaofen, _pn)
        import threading as _th
        _th.Thread(target=task.work).start()
        return json.dumps(
            EasyResponse(ResponseCode.success.value[0], True, ResponseCode.success.value[0], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    finally:
        gc.collect()


@app.route('/easy/query', methods=['GET'])
def easy_query():
    del_flag = False
    get_data = request.args.to_dict()
    try:
        _code = get_data.get('code', '-1')
        if _code == '-1':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'code参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        task_progress = task_dic.get(_code, '-1')
        if task_progress != '-1':
            d = task_progress
            _status = d[0]
            _progress = d[1]
            _result = d[2]
            _msg = d[3]
            if _status == Status.run:
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.success:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg, 'cost': d[4],
                        "video_duration": d[5], "width": d[6], "height": d[7]
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.error:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        else:
            return json.dumps(
                EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    finally:
        if del_flag:
            try:
                del task_dic[_code]
            except Exception as e:
                traceback.print_exc()
                return json.dumps(
                    EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)


# ======================================================================
# Streaming endpoints — Aiszr real-time lip-sync (direct netG, float16)
# ======================================================================

def _detect_face_info(scrfd, frame, img_size, pad, fallback=None):
    bboxes, kps = scrfd.detect(frame, thresh=0.5)
    fh, fw = frame.shape[:2]
    if bboxes is None or len(bboxes) == 0:
        if fallback is not None:
            return fallback
        logger.warning("No face detected, using center crop")
        cx, cy = fw // 2, fh // 2
        sz = min(fw, fh) // 4
        return (cx - sz, cy - sz, cx + sz, cy + sz, None)

    b = bboxes[0]
    x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
    cx1 = max(0, x1 - pad)
    cy1 = max(0, y1 - pad)
    cx2 = min(fw, x2 + pad)
    cy2 = min(fh, y2 + pad)
    face_kps = kps[0] if kps is not None and len(kps) > 0 else None
    return (cx1, cy1, cx2, cy2, face_kps)


def _fallback_crop_landmarks(out_size):
    crop_lm = np.zeros((68, 2), dtype=np.float32)
    xs = np.linspace(out_size * 0.16, out_size * 0.84, 17)
    jaw_t = np.linspace(0, np.pi, 17)
    ys = out_size * (0.36 + 0.50 * np.sin(jaw_t))
    crop_lm[:17, 0] = xs
    crop_lm[:17, 1] = ys
    crop_lm[27] = (out_size * 0.50, out_size * 0.42)
    crop_lm[28] = (out_size * 0.50, out_size * 0.50)
    crop_lm[29] = (out_size * 0.50, out_size * 0.56)
    crop_lm[30] = (out_size * 0.50, out_size * 0.62)
    crop_lm[36] = (out_size * 0.35, out_size * 0.38)
    crop_lm[45] = (out_size * 0.65, out_size * 0.38)
    crop_lm[48] = (out_size * 0.38, out_size * 0.70)
    crop_lm[54] = (out_size * 0.62, out_size * 0.70)
    return crop_lm


def _make_crop_landmarks(crop_img, lm_model):
    try:
        crop_lm = np.asarray(lm_model.forward(crop_img), dtype=np.float32).reshape(-1, 68, 2)[0]
        if np.isfinite(crop_lm).all() and crop_lm.shape == (68, 2):
            return crop_lm
    except Exception:
        logger.exception("PFLD landmark detection failed, using approximate landmarks")
    return _fallback_crop_landmarks(crop_img.shape[0])


def _make_face_data(frame, face_info, img_size, pad, lm_model):
    cx1, cy1, cx2, cy2, _face_kps = face_info
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        raise ValueError(f"empty face crop ({cx1},{cy1},{cx2},{cy2})")
    out_size = img_size + 2 * pad
    crop_img = cv2.resize(crop, (out_size, out_size), interpolation=cv2.INTER_LANCZOS4)
    crop_lm = _make_crop_landmarks(crop_img, lm_model)
    return {'crop_img': crop_img, 'crop_lm': crop_lm}, (cx1, cy1, cx2, cy2)


def _make_mouth_blend_mask(crop_lm, img_size, pad):
    mouth = np.asarray(crop_lm[48:68], dtype=np.float32).copy()
    mouth[:, 0] -= pad
    mouth[:, 1] -= pad
    mouth[:, 0] = np.clip(mouth[:, 0], 0, img_size - 1)
    mouth[:, 1] = np.clip(mouth[:, 1], 0, img_size - 1)

    mask = np.zeros((img_size, img_size), dtype=np.uint8)
    hull = cv2.convexHull(mouth.astype(np.int32))
    cv2.fillConvexPoly(mask, hull, 255)

    dilate_w = max(9, int(img_size * 0.14) | 1)
    dilate_h = max(7, int(img_size * 0.10) | 1)
    blur = max(7, int(img_size * 0.07) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_w, dilate_h))
    mask = cv2.dilate(mask, kernel, iterations=1)
    mask = cv2.GaussianBlur(mask, (blur, blur), 0)
    return mask.astype(np.float32) / 255.0


def _sharpen_mouth_region(inner_bgr, weight):
    alpha = np.clip(weight * 1.35, 0.0, 1.0).astype(np.float32)
    if alpha.max() <= 0:
        return inner_bgr
    blurred = cv2.GaussianBlur(inner_bgr, (0, 0), 0.75)
    sharpened = cv2.addWeighted(inner_bgr, 1.45, blurred, -0.45, 0)
    alpha = alpha[:, :, np.newaxis]
    return np.clip(sharpened * alpha + inner_bgr * (1.0 - alpha), 0, 255).astype(np.uint8)


def _crop_bgr_to_cuda_rgb_chw(crop_bgr):
    crop_rgb = crop_bgr[:, :, (2, 1, 0)].transpose(2, 0, 1).copy()
    return torch.from_numpy(crop_rgb).unsqueeze(0).float().cuda()


def _make_direct_face_cache(model, face_data, img_size, pad):
    crop_img = face_data['crop_img']
    crop_lm = face_data['crop_lm']
    inner = crop_img[pad:-pad, pad:-pad]
    if inner.shape[:2] != (img_size, img_size):
        inner = cv2.resize(inner, (img_size, img_size), interpolation=cv2.INTER_LANCZOS4)

    inner_t = _crop_bgr_to_cuda_rgb_chw(inner)
    mask_cuda = model.mask_cuda.repeat(1, 3, 1, 1)
    mask_re_cuda = model.mask_re_cuda.repeat(1, 3, 1, 1)

    mask_b = model.tensor_norm_no_training(inner_t, mask=mask_cuda).half()
    b_img_re = model.tensor_norm_no_training(inner_t.clone(), mask=mask_re_cuda).half()
    b_img_full = model.tensor_norm_no_training(inner_t.clone()).half()

    weight = _make_mouth_blend_mask(crop_lm, img_size, pad)
    weight_t = torch.from_numpy(weight).unsqueeze(0).unsqueeze(0).cuda().repeat(1, 3, 1, 1).half()

    return {
        'crop_img': crop_img,
        'mask_b': mask_b,
        'b_img_re': b_img_re,
        'b_img_full': b_img_full,
        'weight': weight_t,
        'weight_cpu': weight,
    }


def _infer_face_direct(model, audio_feature, face_cache, pad):
    lab = np.asarray(audio_feature.T, dtype=np.float32)
    lab_t = torch.from_numpy(lab).unsqueeze(0).cuda().half()
    with torch.no_grad():
        fake = model.model.netG(face_cache['mask_b'], face_cache['b_img_re'], lab_t)
        if model.nblend:
            fake = torch.where(model.mask_re_cuda == 0, face_cache['b_img_full'], fake)
        fused = fake * face_cache['weight'] + (1 - face_cache['weight']) * face_cache['b_img_full']
    inner_bgr = (
        (fused[0] * 255)
        .permute(1, 2, 0)
        .byte()
        .cpu()
        .numpy()[:, :, (2, 1, 0)]
    )
    inner_bgr = _sharpen_mouth_region(inner_bgr, face_cache['weight_cpu'])
    crop_bgr = face_cache['crop_img'].copy()
    if pad > 0:
        crop_bgr[pad:-pad, pad:-pad] = inner_bgr
    else:
        crop_bgr[:, :] = inner_bgr
    return crop_bgr


def _pre_extract_audio_features(wav_path, fps=25):
    """Extract wenet features for the entire WAV file via dedicated subprocess."""
    global _audio_feat_q_in, _audio_feat_q_out
    code = os.path.splitext(os.path.basename(wav_path))[0]
    os.makedirs(f'/code/data/temp/{code}', exist_ok=True)
    _audio_feat_q_in.put([code, wav_path, fps])
    result = _audio_feat_q_out.get(timeout=120)
    audio_npy = np.load(result[2])
    return [audio_npy[i] for i in range(audio_npy.shape[0])]


@app.route('/aiszr/stream/start', methods=['POST'])
def aiszr_stream_start():
    data = json.loads(request.data)
    avatar_path = data.get('avatar_video_path', '')
    wav_path = data.get('wav_path', '')
    sample_rate = data.get('sample_rate', 24000)
    target_fps = data.get('target_fps', 25)

    if not avatar_path or not os.path.isfile(avatar_path):
        return json.dumps({'error': f'avatar not found: {avatar_path}'}), 400

    global _stream_session_seq
    with _stream_lock:
        _stream_session_seq += 1
        sid = f"s{_stream_session_seq}"

    model = _load_model()
    lm_model = _load_landmark_model()

    img_size = model.img_size
    pad = int(5 * img_size / 256)

    # Face detect
    cap = cv2.VideoCapture(avatar_path)
    ret, first_frame = cap.read()
    cap.release()
    if not ret:
        return json.dumps({'error': 'cannot read avatar video'}), 400

    from face_detect_utils.scrfd import SCRFD
    scrfd = SCRFD('face_detect_utils/resources/scrfd_500m_bnkps_shape640x640.onnx')
    first_face_info = _detect_face_info(scrfd, first_frame, img_size, pad)
    cx1, cy1, cx2, cy2, _ = first_face_info
    crop_w = cx2 - cx1
    crop_h = cy2 - cy1

    # Pre-extract audio features for the entire WAV (~2s for 90s audio)
    audio_features = []
    wav_exists = os.path.isfile(wav_path) if wav_path else "N/A"
    logger.info(f"aiszr wav_path={wav_path} isfile={wav_exists}")
    if wav_path and os.path.isfile(wav_path):
        t0 = time.time()
        audio_features = _pre_extract_audio_features(wav_path, fps=target_fps)
        logger.info(f"pre-extracted {len(audio_features)} audio features in {time.time() - t0:.2f}s")
    else:
        logger.warning("no wav_path provided or file not found, streaming will return empty frames")

    # Pre-read avatar frames and per-frame face crops for HeyGem's own lmk blend.
    t0_frames = time.time()
    avatar_cap = cv2.VideoCapture(avatar_path)
    avatar_frames = []
    face_data_list = []
    face_boxes = []
    last_face_info = first_face_info
    frame_idx = 0
    while True:
        ret, frm = avatar_cap.read()
        if not ret:
            break
        avatar_frames.append(frm)
        if frame_idx % 6 == 0:
            last_face_info = _detect_face_info(scrfd, frm, img_size, pad, fallback=last_face_info)
        face_data, face_box = _make_face_data(frm, last_face_info, img_size, pad, lm_model)
        face_data_list.append(face_data)
        face_boxes.append(face_box)
        frame_idx += 1
    avatar_cap.release()
    if not avatar_frames:
        return json.dumps({'error': 'avatar has no frames'}), 400
    frame_h, frame_w = avatar_frames[0].shape[:2]
    logger.info(
        f"pre-read {len(avatar_frames)} avatar frames and face crops "
        f"{frame_w}x{frame_h} in {time.time() - t0_frames:.2f}s"
    )

    t0_cache = time.time()
    direct_face_cache = []
    try:
        with torch.no_grad():
            for face_data in face_data_list:
                direct_face_cache.append(_make_direct_face_cache(model, face_data, img_size, pad))
        logger.info(
            f"built GPU face cache frames={len(direct_face_cache)} "
            f"in {time.time() - t0_cache:.2f}s"
        )
    except Exception:
        logger.exception("GPU face cache build failed, falling back to official lmk path")
        direct_face_cache = []

    # Warm up the fastest available path.
    if audio_features and direct_face_cache:
        _ = _infer_face_direct(model, audio_features[0], direct_face_cache[0], pad)
        torch.cuda.synchronize()
    elif audio_features and face_data_list:
        with torch.no_grad():
            _ = model.inference_notraining([audio_features[0]], {0: face_data_list[0]}, 1, 0, 'lmk', {}, 0)
        torch.cuda.synchronize()

    _stream_sessions[sid] = {
        'avatar_path': avatar_path,
        'sample_rate': sample_rate,
        'target_fps': target_fps,
        'frame_id': 0,
        'chunk_seq': 0,
        'cx1': cx1, 'cy1': cy1, 'cx2': cx2, 'cy2': cy2,
        'crop_w': crop_w, 'crop_h': crop_h,
        'img_size': img_size,
        'pad': pad,
        'audio_features': audio_features,
        'avatar_frames': avatar_frames,
        'face_data_list': face_data_list,
        'direct_face_cache': direct_face_cache,
        'face_boxes': face_boxes,
        'frame_w': frame_w, 'frame_h': frame_h,
    }
    logger.info(
        f"aiszr stream start sid={sid} face=({cx1},{cy1},{cx2},{cy2}) "
        f"frames={len(avatar_frames)} size={frame_w}x{frame_h} features={len(audio_features)}"
    )

    return json.dumps({
        'session_id': sid,
        'crop_x': 0,
        'crop_y': 0,
        'crop_w': frame_w,
        'crop_h': frame_h,
    })


@app.route('/aiszr/stream/<sid>/infer', methods=['POST'])
def aiszr_stream_infer(sid: str):
    sess = _stream_sessions.get(sid)
    if sess is None:
        return json.dumps({'error': 'session not found'}), 404

    pcm = request.get_data()
    if not pcm:
        return Response(b'', mimetype='application/octet-stream')

    if not _gpu_lock.acquire(blocking=True, timeout=120):
        return json.dumps({'error': 'GPU busy'}), 503

    try:
        return _do_infer_direct(sess, sid, pcm)
    finally:
        _gpu_lock.release()


def _do_infer_direct(sess, sid, pcm):
    """Full-frame compositing via HeyGem's own lmk blend path."""
    sess['chunk_seq'] += 1
    chunk_seq = sess['chunk_seq']
    sample_rate = sess.get('sample_rate', 24000)
    target_fps = sess.get('target_fps', 25)
    model = _load_model()

    audio_features = sess.get('audio_features', [])
    if not audio_features:
        if chunk_seq <= 2:
            logger.warning(f"aiszr infer sid={sid} seq={chunk_seq} NO features, returning empty")
        return Response(b'', mimetype='application/octet-stream')

    chunk_duration_ms = len(pcm) / 2 / sample_rate * 1000
    cumulative_ms = (chunk_seq - 1) * chunk_duration_ms
    base_idx = int(cumulative_ms * target_fps / 1000) % len(audio_features)
    n_frames = max(1, int(chunk_duration_ms * target_fps / 1000))

    frame_w = sess['frame_w']
    frame_h = sess['frame_h']
    frame_size = frame_w * frame_h * 3
    avatar_frames = sess['avatar_frames']
    face_data_list = sess['face_data_list']
    direct_face_cache = sess.get('direct_face_cache', [])
    face_boxes = sess['face_boxes']

    buf = bytearray(frame_size * n_frames)
    t0 = time.time()
    feature_indices = [(base_idx + i) % len(audio_features) for i in range(n_frames)]
    frame_indices = [feature_idx % len(avatar_frames) for feature_idx in feature_indices]

    for i in range(n_frames):
        feature_idx = feature_indices[i]
        frame_idx = frame_indices[i]
        full = avatar_frames[frame_idx].copy()
        bx1, by1, bx2, by2 = face_boxes[frame_idx]
        bw, bh = bx2 - bx1, by2 - by1

        try:
            if direct_face_cache:
                face_bgr = _infer_face_direct(
                    model, audio_features[feature_idx], direct_face_cache[frame_idx], sess['pad']
                )
            else:
                face_data = face_data_list[frame_idx]
                with torch.no_grad():
                    result = model.inference_notraining(
                        [audio_features[feature_idx]], {0: face_data}, 1, 0, 'lmk', {}, 0
                    )
                face_bgr = result[0] if isinstance(result, list) else result
                face_bgr = np.asarray(face_bgr)
                if face_bgr.dtype != np.uint8:
                    face_bgr = np.clip(face_bgr, 0, 255).astype(np.uint8)
            if face_bgr.shape[:2] != (bh, bw):
                face_bgr = cv2.resize(face_bgr, (bw, bh), interpolation=cv2.INTER_LANCZOS4)
            # Both the GPU cache path and inference_notraining return BGR crops.
            full[by1:by2, bx1:bx2] = face_bgr
        except Exception:
            if chunk_seq <= 3:
                logger.exception(f"official lmk blend failed sid={sid} seq={chunk_seq} frame={i}")

        # BGR → RGB for output
        full_rgb = full[:, :, (2, 1, 0)]
        off = i * frame_size
        buf[off:off + frame_size] = full_rgb.tobytes()

    sess['frame_id'] += n_frames
    elapsed = time.time() - t0
    if chunk_seq <= 3:
        logger.info(
            f"aiszr infer sid={sid} seq={chunk_seq} n_frames={n_frames} "
            f"in {elapsed * 1000:.0f}ms ({elapsed * 1000 / n_frames:.0f}ms/frame)"
        )

    return Response(bytes(buf), mimetype='application/octet-stream')


@app.route('/aiszr/stream/<sid>/stop', methods=['POST'])
def aiszr_stream_stop(sid: str):
    sess = _stream_sessions.pop(sid, None)
    if sess is not None:
        # Clear pre-extracted audio features and frame cache references.
        sess.pop('audio_features', None)
        sess.pop('face_data_list', None)
        sess.pop('direct_face_cache', None)
        sess.pop('avatar_frames', None)
        torch.cuda.empty_cache()
        logger.info(f"aiszr stream stop sid={sid}")
    return json.dumps({'ok': True})


@app.route('/aiszr/health', methods=['GET'])
def aiszr_health():
    return json.dumps({
        'ok': True,
        'streaming_sessions': len(_stream_sessions),
        'model_loaded': _global_model is not None,
    })


# ======================================================================
# Main
# ======================================================================

if __name__ == '__main__':
    from face_detect_utils.face_detect import FaceDetect, pfpld
    import service.trans_dh_service as _tds
    _tds.FaceDetect = FaceDetect
    _tds.pfpld = pfpld

    # Disable chaofen (super-resolution) for streaming performance
    import configparser
    _cfg = configparser.ConfigParser()
    _cfg.read('config/config.ini')
    if _cfg.getint('digital', 'chaofen', fallback=0) != 0:
        _cfg.set('digital', 'chaofen', '0')
        with open('config/config.ini', 'w') as _f:
            _cfg.write(_f)
        logger.info("disabled chaofen in config.ini for streaming performance")

    # Initialize audio models (wenet etc.) before init_p()
    a()
    # Fork batch subprocesses before loading CUDA model
    init_p()

    # Start dedicated audio feature extraction subprocess for streaming
    _audio_feat_q_in = multiprocessing.Queue()
    _audio_feat_q_out = multiprocessing.Queue()
    _audio_feat_proc = multiprocessing.Process(
        target=get_aud_feat1, args=(_audio_feat_q_in, _audio_feat_q_out),
        daemon=True, name='aiszr_audio_feat',
    )
    _audio_feat_proc.start()
    logger.info(f"audio feature subprocess started pid={_audio_feat_proc.pid}")

    time.sleep(5)

    # Load DINet model once
    _load_model()

    logger.info("******************* Unified entry (batch+direct-stream-f16) starting *******************")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)

    app.run(host=str(server_ip), port=int(server_port), debug=False, threaded=True)
