# keyframe_logic.py
import struct

class KeyframeEncoder:
    """
    Replicates the keyframe encoding logic from AtomAnimationSerializer.cs.
    """
    @staticmethod
    def encode_keyframe(time: float, value: float, curve_type: int, last_v: float, last_c: int) -> str:
        """Encodes a single keyframe into the plugin's string format."""
        sb = []
        has_value = abs(last_v - value) > 1e-7
        has_curve_type = last_c != curve_type
        encoded_value = 0
        if has_value: encoded_value |= (1 << 0)
        if has_curve_type: encoded_value |= (1 << 1)
        sb.append(chr(ord('A') + encoded_value))
        sb.append(struct.pack('<f', time).hex().upper())
        if has_value:
            sb.append(struct.pack('<f', value).hex().upper())
        if has_curve_type:
            sb.append(struct.pack('<B', curve_type).hex().upper())
        return "".join(sb)

class KeyframeDecoder:
    """
    Replicates the keyframe decoding logic from AtomAnimationSerializer.cs.
    """
    @staticmethod
    def decode_keyframe(encoded_str: str, last_v: float, last_c: int) -> tuple[float, float, int]:
        """Decodes a single keyframe from the plugin's string format."""
        if not encoded_str: raise ValueError("Encoded string is empty")
        flag_char = encoded_str[0]
        encoded_value = ord(flag_char) - ord('A')
        has_value = (encoded_value & (1 << 0)) != 0
        has_curve_type = (encoded_value & (1 << 1)) != 0
        ptr = 1
        time_hex = encoded_str[ptr:ptr+8]
        time = struct.unpack('<f', bytes.fromhex(time_hex))[0]
        ptr += 8
        value = last_v
        if has_value:
            value_hex = encoded_str[ptr:ptr+8]
            value = struct.unpack('<f', bytes.fromhex(value_hex))[0]
            ptr += 8
        curve_type = last_c
        if has_curve_type:
            curve_type_hex = encoded_str[ptr:ptr+2]
            curve_type = struct.unpack('<B', bytes.fromhex(curve_type_hex))[0]
            ptr += 2
        return time, value, curve_type