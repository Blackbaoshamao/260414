from tests.test_decoder import _build_test_frame


def test_follow_fallback_is_disabled_to_avoid_fake_nickname():
    from decoder import DanmakuDecoder

    decoder = DanmakuDecoder()
    # Malformed payload triggers parser exception path.
    raw = _build_test_frame([("WebcastSocialMessage", b"\xff")])
    result = decoder.decode(raw)

    assert result.messages == []
    assert result.parse_failures >= 1
    stats = decoder.method_stats_snapshot()
    assert stats["parse_fail_counts"]["WebcastSocialMessage"] >= 1


def test_follow_fallback_recovers_real_nickname_from_social_template():
    from decoder import DanmakuDecoder

    payload_hex = (
        "0aa1120a1457656263617374536f6369616c4d65737361676510aa969cac81e7fcf069"
        "18a396eaf2968ff0f069300142ea110a0f726f6f6d5f666f6c6c6f775f6d736712187b30"
        "3a757365727d20e585b3e6b3a8e4ba86e4b8bbe692ad1a0c0a0723384345374646209003"
        "22ae11080baa01a8110aa51108bee8c0d9a6f5c10610a9de80cf0d1a0fe4ba91e6b7b1e4"
        "baa6e6b2bee8a1a320014add030a820168747470733a2f2f7031312e646f7579696e7069"
        "632e636f6d2f6177656d652f313030783130302f6177656d652d6176617461722f746f73"
        "2d636e2d692d30383133633030315f6f456f37594141426f43413576534e344365464567"
        "32444e41754139523341494569666b4e4f2e6a7065673f66726f6d3d3330363736373133"
        "33340a820168747470733a2f2f7032362e646f7579696e7069632e636f6d2f6177656d65"
        "2f313030783130302f6177656d652d6176617461722f746f732d636e2d692d3038313363"
        "3030315f6f456f37594141426f43413576534e34436546456732444e4175413952334149"
        "4569666b4e4f2e6a7065673f66726f6d3d333036373637313333340a810168747470733a"
        "2f2f70332e646f7579696e7069632e636f6d2f6177656d652f313030783130302f617765"
        "6d652d6176617461722f746f732d636e2d692d30383133633030315f6f456f3759414142"
        "6f43413576534e34436546456732444e41754139523341494569666b4e4f2e6a7065673f"
        "66726f6d3d33303637363731333334"
    )

    decoder = DanmakuDecoder()
    raw = _build_test_frame([("WebcastSocialMessage", bytes.fromhex(payload_hex))])
    result = decoder.decode(raw)

    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg["type"] == "follow"
    assert msg["nickname"] == "\u4e91\u6df1\u4ea6\u6cbe\u8863"
    assert msg["user_id"] == ""


def test_frame_decode_failure_increments_decode_fail():
    from decoder import DanmakuDecoder

    decoder = DanmakuDecoder()
    result = decoder.decode(b"\x00\x01\x02")  # malformed full frame
    assert result.messages == []
    assert not result.need_ack
