#!/usr/bin/env python
# coding=utf-8
"""Unified Docker entry: batch (:8383) + streaming inference endpoints.

Drop-in replacement for app_local.py. Adds three HTTP endpoints that
Aiszr's server.py (Windows host, FastAPI :8770) calls for per-chunk
real-time lip-sync inference.

Protocol:
  POST /aiszr/stream/start  body={avatar_video_path, sample_rate, target_fps}
       → {session_id, crop_x, crop_y, crop_w, crop_h}
  POST /aiszr/stream/<sid>/infer  body=raw PCM s16le mono
       → raw RGB bytes (crop_w * crop_h * 3, H×W×3 uint8)
  POST /aiszr/stream/<sid>/stop
       → {ok: true}

Batch endpoints (/easy/submit, /easy/query) unchanged from app_local.py.
"""
import os
os.chdir('/code')

import gc
import json
import struct
import threading
import time
import traceback
from enum import Enum

import cv2
import numpy as np
from flask import Flask, Response, request
from service.config import result_dir, server_ip, server_port, temp_dir
from service.self_logger import logger
from service.trans_dh_service import (Status, TransDhTask, a, get_run_flag,
                                       init_p, task_dic)

app = Flask(__name__)

# Serialize GPU work between batch workers and streaming.
_gpu_lock = threading.Lock()

# ── Streaming session state ──────────────────────────────────────────────────
_stream_sessions: dict = {}
_stream_session_seq = 0
_stream_lock = threading.Lock()


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
            default=lambda obj: obj.__dict__,
            sort_keys=True, ensure_ascii=False, indent=4)

    try:
        if 'audio_url' not in request_data or request_data['audio_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'audio_url参数缺失', {}),
                default=lambda obj: obj.__dict__,
                sort_keys=True, ensure_ascii=False, indent=4)
        if 'video_url' not in request_data or request_data['video_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'video_url参数缺失', {}),
                default=lambda obj: obj.__dict__,
                sort_keys=True, ensure_ascii=False, indent=4)
        if 'code' not in request_data or request_data['code'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'code参数缺失', {}),
                default=lambda obj: obj.__dict__,
                sort_keys=True, ensure_ascii=False, indent=4)
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
            default=lambda obj: obj.__dict__,
            sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__,
            sort_keys=True, ensure_ascii=False, indent=4)
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
                default=lambda obj: obj.__dict__,
                sort_keys=True, ensure_ascii=False, indent=4)
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
                    default=lambda obj: obj.__dict__,
                    sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.success:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg, 'cost': d[4],
                        "video_duration": d[5], "width": d[6], "height": d[7]
                    }),
                    default=lambda obj: obj.__dict__,
                    sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.error:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg
                    }),
                    default=lambda obj: obj.__dict__,
                    sort_keys=True, ensure_ascii=False, indent=4)
        else:
            return json.dumps(
                EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                default=lambda obj: obj.__dict__,
                sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__,
            sort_keys=True, ensure_ascii=False, indent=4)
    finally:
        if del_flag:
            try:
                del task_dic[_code]
            except Exception as e:
                traceback.print_exc()
                return json.dumps(
                    EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                    default=lambda obj: obj.__dict__,
                    sort_keys=True, ensure_ascii=False, indent=4)


# ======================================================================
# Streaming endpoints — Aiszr real-time lip-sync
# ======================================================================

# Mock crop (will be replaced by real DigitalHumanModel init)
_MOCK_CROP_W = 160
_MOCK_CROP_H = 96
_MOCK_CROP_X = 220
_MOCK_CROP_Y = 540


@app.route('/aiszr/stream/start', methods=['POST'])
def aiszr_stream_start():
    """Init streaming session: preload avatar face data via batch preprocess."""
    data = json.loads(request.data)
    avatar_path = data.get('avatar_video_path', '')
    sample_rate = data.get('sample_rate', 24000)
    target_fps = data.get('target_fps', 25)

    if not avatar_path or not os.path.isfile(avatar_path):
        return json.dumps({'error': f'avatar not found: {avatar_path}'}), 400

    global _stream_session_seq
    with _stream_lock:
        _stream_session_seq += 1
        sid = f"s{_stream_session_seq}"

    # Read first frame to determine crop region from face detection
    cap = cv2.VideoCapture(avatar_path)
    ret, first_frame = cap.read()
    cap.release()

    crop_x, crop_y, crop_w, crop_h = _MOCK_CROP_X, _MOCK_CROP_Y, _MOCK_CROP_W, _MOCK_CROP_H
    if ret:
        frame_h, frame_w = first_frame.shape[:2]
        # Use face detection to find crop region
        try:
            from face_detect_utils.face_detect import FaceDetect
            fd = FaceDetect(model_path='/code/face_detect_utils/resources/')
            faces = fd.get_bboxes(first_frame)
            if len(faces) > 0:
                bbox = faces[0]
                x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                # Expand to 256x256 centered on face
                face_w = x2 - x1
                face_h = y2 - y1
                # Use lower 60% of face as mouth region
                mouth_y1 = y1 + int(face_h * 0.55)
                mouth_y2 = y2
                mouth_x1 = max(0, x1 - int(face_w * 0.1))
                mouth_x2 = min(frame_w, x2 + int(face_w * 0.1))
                crop_w = mouth_x2 - mouth_x1
                crop_h = mouth_y2 - mouth_y1
                crop_x = mouth_x1
                crop_y = mouth_y1
        except Exception as e:
            logger.warning("face detect fallback to mock crop: {}", e)

    _stream_sessions[sid] = {
        'avatar_path': avatar_path,
        'sample_rate': sample_rate,
        'target_fps': target_fps,
        'frame_id': 0,
        'crop_x': crop_x,
        'crop_y': crop_y,
        'crop_w': crop_w,
        'crop_h': crop_h,
        'chunk_seq': 0,
    }
    logger.info("aiszr stream start sid={} avatar={} crop=({},{},{},{})",
                sid, avatar_path, crop_x, crop_y, crop_w, crop_h)

    return json.dumps({
        'session_id': sid,
        'crop_x': crop_x,
        'crop_y': crop_y,
        'crop_w': crop_w,
        'crop_h': crop_h,
    })


@app.route('/aiszr/stream/<sid>/infer', methods=['POST'])
def aiszr_stream_infer(sid: str):
    """Run inference on one PCM chunk via micro-batch pipeline.

    Flow: PCM → WAV → TransDhTask → result video → extract mouth crop → RGB bytes.
    """
    sess = _stream_sessions.get(sid)
    if sess is None:
        return json.dumps({'error': 'session not found'}), 404

    pcm = request.get_data()
    if not pcm:
        return Response(b'', mimetype='application/octet-stream')

    sess['chunk_seq'] += 1
    chunk_seq = sess['chunk_seq']
    sample_rate = sess.get('sample_rate', 24000)

    # Save PCM to temp WAV
    chunk_code = f"{sid}_c{chunk_seq}"
    chunk_dir = os.path.join(temp_dir, chunk_code)
    os.makedirs(chunk_dir, exist_ok=True)
    wav_path = os.path.join(chunk_dir, 'audio.wav')

    import wave as wave_mod
    with wave_mod.open(wav_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

    # Submit internal batch task
    task = TransDhTask(
        chunk_code, wav_path, sess['avatar_path'],
        0, 0, 0, 0,
    )

    task.preprocess()
    task.work()

    # Wait for final output MP4 (work() kicks off async workers via queues)
    result_mp4 = os.path.join(temp_dir, f"{chunk_code}-r.mp4")
    for _ in range(900):  # up to 90s
        if os.path.isfile(result_mp4) and os.path.getsize(result_mp4) > 0:
            break
        time.sleep(0.1)
    else:
        logger.warning("result mp4 timeout for {}", chunk_code)

    if not os.path.isfile(result_mp4):
        # Fallback to mock
        logger.warning("no result mp4 for {}, fallback mock", chunk_code)
        samples = np.frombuffer(pcm, dtype=np.int16)
        amp = float(np.abs(samples).mean()) / 32768.0
        h = max(2, int(sess['crop_h'] * min(0.9, 0.15 + amp * 2.0)))
        skin = np.full((sess['crop_h'], sess['crop_w'], 3), (220, 180, 160), dtype=np.uint8)
        skin[sess['crop_h'] - h:, :] = (40, 20, 30)
        return Response(skin.tobytes(), mimetype='application/octet-stream')

    # Extract mouth crop from first frame of result MP4
    cap = cv2.VideoCapture(result_mp4)
    ret, result_frame = cap.read()
    cap.release()

    if not ret:
        return Response(b'', mimetype='application/octet-stream')

    # Extract crop region
    cx, cy = sess['crop_x'], sess['crop_y']
    cw, ch = sess['crop_w'], sess['crop_h']
    fh, fw = result_frame.shape[:2]

    # Clamp crop bounds
    y1 = max(0, cy)
    y2 = min(fh, cy + ch)
    x1 = max(0, cx)
    x2 = min(fw, cx + cw)

    crop = result_frame[y1:y2, x1:x2]

    # Resize to expected crop size if needed
    if crop.shape[:2] != (ch, cw):
        crop = cv2.resize(crop, (cw, ch))

    # Convert BGR → RGB
    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    sess['frame_id'] += 1

    # Cleanup temp files
    try:
        import shutil
        shutil.rmtree(os.path.join(temp_dir, chunk_code), ignore_errors=True)
        if os.path.isfile(result_mp4):
            os.remove(result_mp4)
    except Exception:
        pass

    return Response(crop_rgb.tobytes(), mimetype='application/octet-stream')


@app.route('/aiszr/stream/<sid>/stop', methods=['POST'])
def aiszr_stream_stop(sid: str):
    """Cleanup streaming session."""
    sess = _stream_sessions.pop(sid, None)
    if sess is not None:
        # TODO: release DigitalHumanModel resources
        logger.info("aiszr stream stop sid={}", sid)
    return json.dumps({'ok': True})


@app.route('/aiszr/health', methods=['GET'])
def aiszr_health():
    return json.dumps({
        'ok': True,
        'streaming_sessions': len(_stream_sessions),
    })


# ── 诊断端点（开发用，生产删）───────────────────────────────────────────────

@app.route('/aiszr/diag/init_wh', methods=['POST'])
def diag_init_wh():
    """Call init_wh with a driver video and return result structure info."""
    from service.trans_dh_service import init_wh
    data = json.loads(request.data)
    driver_path = data.get('driver_path', '/code/data/temp/anchor.mp4')
    code = data.get('code', 'diag_test')
    try:
        result = init_wh(code, driver_path)
        if result is None:
            return json.dumps({'result': None})
        info = {}
        if isinstance(result, dict):
            for k, v in result.items():
                if hasattr(v, 'shape'):
                    info[k] = {'type': 'ndarray', 'shape': list(v.shape), 'dtype': str(v.dtype)}
                elif isinstance(v, (list, tuple)):
                    info[k] = {'type': type(v).__name__, 'len': len(v)}
                    if len(v) > 0 and hasattr(v[0], 'shape'):
                        info[k]['element_shape'] = list(v[0].shape)
                else:
                    info[k] = {'type': type(v).__name__, 'repr': repr(v)[:200]}
        else:
            info['_type'] = type(result).__name__
            info['_repr'] = repr(result)[:500]
        return json.dumps({'result_type': type(result).__name__, 'keys': info})
    except Exception as e:
        return json.dumps({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/aiszr/diag/probe_inference', methods=['POST'])
def diag_probe_inference():
    """Try inference_notraining with dummy data to discover parameter formats."""
    import torch
    from landmark2face_wy.digitalhuman_interface import DigitalHumanModel

    errors = []
    try:
        model = DigitalHumanModel(blend_dynamic=False, chaofen_before=False)
    except Exception as e:
        return json.dumps({'error': f'model init failed: {e}'})

    # Try different parameter combinations
    # inference_notraining(self, audio_info, face_data_dict, this_batch,
    #                      start_idx, blend_dynamic, params, frameId)

    tests = [
        {
            'desc': 'all zeros minimal',
            'audio_info': np.zeros((1, 20, 256), dtype=np.float32),
            'face_data_dict': {},
            'this_batch': {},
            'start_idx': 0,
            'blend_dynamic': False,
            'params': {},
            'frameId': 0,
        },
    ]

    for t in tests:
        try:
            r = model.inference_notraining(
                t['audio_info'], t['face_data_dict'], t['this_batch'],
                t['start_idx'], t['blend_dynamic'], t['params'], t['frameId']
            )
            errors.append({'test': t['desc'], 'result_type': type(r).__name__,
                          'result_repr': repr(r)[:300] if r is not None else 'None'})
        except Exception as e:
            errors.append({'test': t['desc'], 'error': str(e)})

    # Also try with None
    try:
        r = model.inference_notraining(None, None, None, 0, False, None, 0)
        errors.append({'test': 'all None', 'result': repr(r)[:200]})
    except Exception as e:
        errors.append({'test': 'all None', 'error': str(e)})

    # Try with simple numpy
    try:
        r = model.inference_notraining(
            np.zeros(10), np.zeros(10), np.zeros(10),
            0, False, np.zeros(10), 0
        )
        errors.append({'test': '1d numpy', 'result': repr(r)[:200]})
    except Exception as e:
        errors.append({'test': '1d numpy', 'error': str(e)})

    # Try this_batch=int
    for tb in [1, 16]:
        try:
            r = model.inference_notraining(
                np.zeros((1, 20, 256), dtype=np.float32),
                {}, tb, 0, False, {}, 0
            )
            errors.append({'test': f'this_batch={tb}, empty dicts', 'result': repr(r)[:300]})
        except Exception as e:
            errors.append({'test': f'this_batch={tb}, empty dicts', 'error': str(e)})

    # Try face_data_dict with common keys from batch pipeline
    face_dict = {
        'img_paths': ['/code/data/temp/anchor.mp4'],
        'crop_images': np.zeros((1, 256, 256, 3), dtype=np.uint8),
    }
    try:
        r = model.inference_notraining(
            np.zeros((1, 20, 256), dtype=np.float32),
            face_dict, 1, 0, False, {}, 0
        )
        errors.append({'test': 'face_dict with crop_images', 'result': repr(r)[:300]})
    except Exception as e:
        errors.append({'test': 'face_dict with crop_images', 'error': str(e)})

    # Try torch tensors instead of numpy
    import torch
    try:
        r = model.inference_notraining(
            torch.zeros(1, 20, 256),
            {}, 1, 0, False, {}, 0
        )
        errors.append({'test': 'torch audio tensor', 'result': repr(r)[:300]})
    except Exception as e:
        errors.append({'test': 'torch audio tensor', 'error': str(e)})

    return json.dumps({'results': errors})


@app.route('/aiszr/diag/probe_inference2', methods=['POST'])
def diag_probe_inference2():
    """Try inference_notraining with real face_data_dict keys from .so strings."""
    import torch
    from landmark2face_wy.digitalhuman_interface import DigitalHumanModel

    try:
        model = DigitalHumanModel(blend_dynamic=False, chaofen_before=False)
    except Exception as e:
        return json.dumps({'error': f'model init: {e}'})

    errors = []
    # face_data_dict keys from .so: crop_img, crop_lm, landmarks, mask_B,
    # mask_B_list, mask_B_pre, mask_B_pre_list, B_img, B_img_list,
    # blend_mask, blend_mask_list, img_idx, audio_idx, wenet_feature, img_size

    # Read first frame from anchor.mp4 for realistic crop image
    cap = cv2.VideoCapture('/code/data/temp/anchor.mp4')
    ret, frame = cap.read()
    cap.release()
    h, w = frame.shape[:2]

    face_dict = {
        'crop_img': np.zeros((1, 256, 256, 3), dtype=np.uint8),
        'crop_lm': np.zeros((1, 68, 2), dtype=np.float64),
        'landmarks': np.zeros((1, 68, 2), dtype=np.float64),
        'mask_B': np.zeros((1, 256, 256, 1), dtype=np.float32),
        'mask_B_list': [np.zeros((256, 256, 1), dtype=np.float32)],
        'mask_B_pre': np.zeros((1, 256, 256, 1), dtype=np.float32),
        'mask_B_pre_list': [np.zeros((256, 256, 1), dtype=np.float32)],
        'B_img': np.zeros((1, 256, 256, 3), dtype=np.uint8),
        'B_img_list': [np.zeros((256, 256, 3), dtype=np.uint8)],
        'blend_mask': np.zeros((1, 256, 256, 1), dtype=np.float32),
        'blend_mask_list': [np.zeros((256, 256, 1), dtype=np.float32)],
        'img_idx': 0,
        'audio_idx': 0,
        'wenet_feature': np.zeros((20, 256), dtype=np.float32),
        'img_size': 256,
    }

    # audio_info: try different shapes
    audio_tests = [
        ('audio_np_1x20x256', np.zeros((1, 20, 256), dtype=np.float32)),
        ('audio_np_20x256', np.zeros((20, 256), dtype=np.float32)),
        ('audio_torch_1x20x256', torch.zeros(1, 20, 256)),
        ('audio_torch_1x256x20', torch.zeros(1, 256, 20)),
    ]

    for desc, audio in audio_tests:
        try:
            r = model.inference_notraining(
                audio, face_dict, 1, 0, False, face_dict, 0
            )
            errors.append({
                'test': f'{desc}, params=face_dict',
                'result_type': type(r).__name__,
                'result_repr': repr(r)[:300] if r is not None else 'None',
            })
        except Exception as e:
            errors.append({'test': f'{desc}, params=face_dict', 'error': str(e)[:200]})

    # Try params as tuple (maybe from batch pipeline)
    for desc, params in [('params=()', ()), ('params=[]', []), ('params=None', None)]:
        try:
            r = model.inference_notraining(
                np.zeros((1, 20, 256), dtype=np.float32),
                face_dict, 1, 0, False, params, 0
            )
            errors.append({
                'test': f'audio_np_1x20x256, {desc}',
                'result_type': type(r).__name__,
                'result_repr': repr(r)[:300] if r is not None else 'None',
            })
        except Exception as e:
            errors.append({'test': f'audio_np_1x20x256, {desc}', 'error': str(e)[:200]})

    return json.dumps({'results': errors})


@app.route('/aiszr/diag/globals', methods=['GET'])
def diag_globals():
    """Check what globals are set in trans_dh_service after a()+init_p()."""
    import service.trans_dh_service as m
    interesting = {}
    for name in dir(m):
        if name.startswith('_'):
            continue
        obj = getattr(m, name)
        t = type(obj).__name__
        if t in ('CythonFunction', 'cython_function_or_method', 'type', 'function', 'method'):
            interesting[name] = t
        elif hasattr(obj, 'shape'):
            interesting[name] = f'ndarray {obj.shape}'
        elif isinstance(obj, (int, float, str, bool)):
            interesting[name] = f'{t} = {repr(obj)[:100]}'
        elif isinstance(obj, (list, tuple, dict)):
            interesting[name] = f'{t} len={len(obj)}'
    interesting['_has_FaceDetect'] = 'FaceDetect' in dir(m)
    interesting['_FaceDetect_in_dict'] = 'FaceDetect' in m.__dict__
    return json.dumps(interesting, indent=2)


@app.route('/aiszr/diag/intercept_batch', methods=['POST'])
def diag_intercept_batch():
    """Submit a batch task and capture trans_queue data format."""
    from service.trans_dh_service import (
        TransDhTask, audio_queue, audio_feature_queue_output,
        trans_queue, output_queue, init_wh_queue, init_wh_queue_output
    )

    code = 'intercept_' + str(int(time.time()))
    audio_path = '/code/data/temp/bench.wav'
    video_path = '/code/data/temp/anchor.mp4'

    # Clear stale queue items
    for q in [init_wh_queue_output, audio_feature_queue_output, output_queue]:
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break

    captured = {}

    # Start task in thread
    task = TransDhTask(code, audio_path, video_path, 0, 0, 0, 0)

    import threading
    def run_task():
        task.preprocess()
        task.work()

    t = threading.Thread(target=run_task)
    t.start()

    # Capture data from queues with timeout
    # 1. init_wh_queue_output
    try:
        wh_result = init_wh_queue_output.get(timeout=30)
        captured['init_wh_output'] = {
            'type': type(wh_result).__name__,
            'repr': repr(wh_result)[:300],
        }
    except Exception as e:
        captured['init_wh_output_error'] = str(e)

    # 2. audio_feature_queue_output
    try:
        aud_result = audio_feature_queue_output.get(timeout=30)
        if isinstance(aud_result, (list, tuple)):
            items = []
            for item in aud_result:
                if hasattr(item, 'shape'):
                    items.append(f'ndarray shape={item.shape} dtype={item.dtype}')
                elif isinstance(item, dict):
                    items.append(f'dict keys={list(item.keys())[:10]}')
                else:
                    items.append(f'{type(item).__name__} = {repr(item)[:100]}')
            captured['audio_feat_output'] = items
        else:
            captured['audio_feat_output'] = f'{type(aud_result).__name__}: {repr(aud_result)[:200]}'
    except Exception as e:
        captured['audio_feat_output_error'] = str(e)

    # 3. output_queue — first result
    try:
        out_result = output_queue.get(timeout=60)
        if isinstance(out_result, (list, tuple)):
            items = []
            for item in out_result:
                if hasattr(item, 'shape'):
                    items.append(f'ndarray shape={item.shape} dtype={item.dtype}')
                elif isinstance(item, dict):
                    items.append(f'dict keys={list(item.keys())[:10]}')
                else:
                    items.append(f'{type(item).__name__} = {repr(item)[:100]}')
            captured['output_queue_first'] = items
        elif isinstance(out_result, dict):
            info = {}
            for k, v in out_result.items():
                if hasattr(v, 'shape'):
                    info[k] = f'ndarray shape={v.shape} dtype={v.dtype}'
                else:
                    info[k] = f'{type(v).__name__}'
            captured['output_queue_first'] = info
        else:
            captured['output_queue_first'] = f'{type(out_result).__name__}: {repr(out_result)[:300]}'
    except Exception as e:
        captured['output_queue_error'] = str(e)

    t.join(timeout=90)

    return json.dumps(captured, indent=2)


@app.route('/aiszr/diag/audio_queue_probe', methods=['POST'])
def diag_audio_queue_probe():
    """Send audio through audio_queue and capture audio_feature_queue_output format."""
    from service.trans_dh_service import audio_queue, audio_feature_queue_output
    import wave

    # Write PCM from request body to temp WAV
    pcm_data = request.get_data()
    if not pcm_data:
        # Use bench.wav
        pcm_data = open('/code/data/temp/bench.wav', 'rb').read()

    # Actually just send bench.wav path on the queue
    # The queue format is unknown, so try common patterns

    results = {}

    # Try sending [code, audio_path] on audio_queue
    # Clear output first
    while not audio_feature_queue_output.empty():
        try:
            audio_feature_queue_output.get_nowait()
        except Exception:
            break

    test_code = 'audio_probe_' + str(int(time.time()))
    wav_path_16k = '/code/data/temp/bench_16k.wav'
    wav_path_24k = '/code/data/temp/bench.wav'
    temp_base = '/code/data/temp'

    # Create a temp dir for the test
    test_dir = f'{temp_base}/{test_code}'
    os.makedirs(test_dir, exist_ok=True)

    # The batch pipeline format for audio_queue might be:
    # [code, audio_path, output_dir, feature_type, ...]
    patterns = {
        'p3_code_wav_dir': [test_code + '_a', wav_path_24k, test_dir + '/a'],
        'p4_code_wav_dir_feat': [test_code + '_b', wav_path_24k, test_dir + '/b', '3dmm'],
        'p5_code_wav_dir_feat_video': [test_code + '_c', wav_path_24k, test_dir + '/c', '3dmm', '/code/data/temp/anchor.mp4'],
        'p3_16k_code_wav_dir': [test_code + '_d', wav_path_16k, test_dir + '/d'],
        'p4_16k_all': [test_code + '_e', wav_path_16k, test_dir + '/e', '3dmm', '/code/data/temp/anchor.mp4'],
    }

    for desc, item in patterns.items():
        # Clear output
        while not audio_feature_queue_output.empty():
            try:
                audio_feature_queue_output.get_nowait()
            except Exception:
                break
        try:
            audio_queue.put(item, timeout=5)
            aud = audio_feature_queue_output.get(timeout=30)
            results[desc] = _describe(aud)
        except Exception as e:
            results[desc + '_err'] = str(e)[:200]

    return json.dumps(results, indent=2)


def _describe(obj, depth=0):
    """Recursively describe an object's structure."""
    if depth > 3:
        return f'{type(obj).__name__}(...)'
    if isinstance(obj, dict):
        return {k: _describe(v, depth+1) for k, v in list(obj.items())[:10]}
    elif isinstance(obj, (list, tuple)):
        items = [_describe(v, depth+1) for v in obj[:5]]
        if len(obj) > 5:
            items.append(f'...({len(obj)} total)')
        return items
    elif hasattr(obj, 'shape'):
        return f'ndarray({obj.shape}, {obj.dtype})'
    elif isinstance(obj, (int, float, str, bool)):
        return f'{type(obj).__name__}={repr(obj)[:80]}'
    else:
        return type(obj).__name__


@app.route('/aiszr/diag/init_wh_direct', methods=['POST'])
def diag_init_wh_direct():
    """Inject FaceDetect + pfpld into trans_dh_service and call init_wh directly."""
    import service.trans_dh_service as m
    from face_detect_utils.face_detect import FaceDetect, pfpld
    m.FaceDetect = FaceDetect
    m.pfpld = pfpld

    from service.trans_dh_service import init_wh
    data = json.loads(request.data)
    driver_path = data.get('driver_path', '/code/data/temp/anchor.mp4')
    code = data.get('code', 'diag_direct')

    try:
        result = init_wh(code, driver_path)
    except Exception as e:
        return json.dumps({'error': str(e), 'trace': traceback.format_exc()[:1000]})

    info = {}
    if result is None:
        return json.dumps({'result': None})
    if isinstance(result, dict):
        for k, v in result.items():
            if hasattr(v, 'shape'):
                info[k] = {'type': 'ndarray', 'shape': list(v.shape), 'dtype': str(v.dtype)}
            elif isinstance(v, (list, tuple)):
                info[k] = {'type': type(v).__name__, 'len': len(v)}
                if len(v) > 0:
                    if hasattr(v[0], 'shape'):
                        info[k]['element_shape'] = list(v[0].shape)
                    elif isinstance(v[0], dict):
                        info[k]['element_keys'] = list(v[0].keys())[:10]
                    else:
                        info[k]['element_type'] = type(v[0]).__name__
            elif isinstance(v, (int, float, str, bool)):
                info[k] = f'{type(v).__name__} = {repr(v)[:100]}'
            else:
                info[k] = f'{type(v).__name__}'
    else:
        info['_type'] = type(result).__name__
        info['_repr'] = repr(result)[:500]
    return json.dumps({'result_type': type(result).__name__, 'keys': info}, indent=2)
def diag_init_wh_queue():
    """Send avatar video through init_wh_queue and capture face data."""
    from service.trans_dh_service import init_wh_queue, init_wh_queue_output
    data = json.loads(request.data)
    driver_path = data.get('driver_path', '/code/data/temp/anchor.mp4')
    code = data.get('code', 'diag_q_' + str(int(time.time())))

    # Clear any stale output
    while not init_wh_queue_output.empty():
        try:
            init_wh_queue_output.get_nowait()
        except Exception:
            break

    # Send through queue (same as batch TransDhTask.preprocess does)
    try:
        init_wh_queue.put([code, driver_path])
    except Exception as e:
        return json.dumps({'error': f'queue put failed: {e}'})

    # Wait for result (up to 30s)
    result = None
    for _ in range(300):
        try:
            result = init_wh_queue_output.get(timeout=0.1)
            break
        except Exception:
            continue

    if result is None:
        return json.dumps({'error': 'timeout waiting for init_wh_queue_output'})

    info = {}
    if isinstance(result, dict):
        for k, v in result.items():
            if hasattr(v, 'shape'):
                info[k] = {'type': 'ndarray', 'shape': list(v.shape), 'dtype': str(v.dtype)}
            elif isinstance(v, (list, tuple)):
                info[k] = {'type': type(v).__name__, 'len': len(v)}
                if len(v) > 0:
                    if hasattr(v[0], 'shape'):
                        info[k]['element_shape'] = list(v[0].shape)
                    elif isinstance(v[0], dict):
                        info[k]['element_keys'] = list(v[0].keys())[:10]
                    else:
                        info[k]['element_type'] = type(v[0]).__name__
            elif isinstance(v, (int, float, str, bool)):
                info[k] = f'{type(v).__name__} = {repr(v)[:100]}'
            else:
                info[k] = f'{type(v).__name__}'
    else:
        info['_type'] = type(result).__name__
        info['_repr'] = repr(result)[:500]

    return json.dumps({'result_type': type(result).__name__, 'keys': info}, indent=2)


# ── 推理参数拦截 ─────────────────────────────────────────────────────────────
_last_inference_params = {}

_original_inference = None


def _install_inference_sniff():
    """Monkey-patch DigitalHumanModel.inference to capture parameter formats."""
    global _original_inference
    from landmark2face_wy.digitalhuman_interface import DigitalHumanModel
    _original_inference = DigitalHumanModel.inference

    def _sniffed_inference(self, audio_info, face_data_dict, this_batch, start_idx, params):
        global _last_inference_params
        _last_inference_params = {
            'audio_info': _quick_desc(audio_info),
            'face_data_dict': _quick_desc(face_data_dict),
            'this_batch': repr(this_batch),
            'start_idx': repr(start_idx),
            'params': _quick_desc(params),
        }
        return _original_inference(self, audio_info, face_data_dict, this_batch, start_idx, params)

    DigitalHumanModel.inference = _sniffed_inference


def _quick_desc(obj):
    if isinstance(obj, dict):
        return {k: _quick_desc(v) for k, v in list(obj.items())[:10]}
    elif isinstance(obj, (list, tuple)):
        items = [_quick_desc(v) for v in obj[:3]]
        if len(obj) > 3:
            items.append(f'...({len(obj)} total)')
        return items
    elif hasattr(obj, 'shape'):
        return f'ndarray({list(obj.shape)}, {obj.dtype})'
    elif isinstance(obj, (int, float, str, bool)):
        return f'{type(obj).__name__}={repr(obj)[:80]}'
    return type(obj).__name__


@app.route('/aiszr/diag/sniff_inference', methods=['POST'])
def diag_sniff_inference():
    """Submit a batch task with inference sniffing enabled, return captured params."""
    global _last_inference_params
    _last_inference_params = {}

    if _original_inference is None:
        _install_inference_sniff()

    code = 'sniff_' + str(int(time.time()))
    task = TransDhTask(code, '/code/data/temp/bench.wav', '/code/data/temp/anchor.mp4', 0, 0, 0, 0)

    import threading
    def run():
        task.preprocess()
        task.work()
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=120)

    return json.dumps(_last_inference_params, indent=2)


# ======================================================================
# Main
# ======================================================================

if __name__ == '__main__':
    a()
    init_p()
    time.sleep(15)
    logger.info("******************* Unified entry (batch+stream) starting *******************")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)

    # threaded=True: allows concurrent HTTP handling for batch + streaming
    app.run(host=str(server_ip), port=int(server_port), debug=False, threaded=True)
