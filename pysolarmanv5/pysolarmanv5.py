"""pysolarmanv5.py"""
import struct
import socket

from umodbus.client.serial import rtu


class V5FrameError(Exception):
    """V5 Frame Validation Error"""

    pass


class PySolarmanV5:
    """
    The PySolarmanV5 class establishes a TCP connection to a Solarman V5 data
    logging stick and exposes methods to send/receive Modbus RTU requests and
    responses.

    For more detailed information on the Solarman V5 Protocol, see
    :doc:`solarmanv5_protocol`

    :param address: IP address or hostname of data logging stick
    :type address: str
    :param serial: Serial number of the data logging stick (not inverter!)
    :type serial: int
    :param port: TCP port to connect to data logging stick, defaults to 8899
    :type port: int, optional
    :param mb_slave_id: Inverter Modbus slave ID, defaults to 1
    :type mb_slave_id: int, optional
    :param verbose: Enable verbose logging, defaults to False
    :type verbose: bool, optional
    :param socket_timeout: Socket timeout duration in seconds, defaults to 60
    :type socket_timeout: int, optional
    :param v5_error_correction: Enable naive error correction for V5 frames,
        defaults to False
    :type v5_error_correction: bool, optional

    Basic example:
       >>> from pysolarmanv5 import PySolarmanV5
       >>> modbus = PySolarmanV5("192.168.1.10", 123456789)
       >>> print(modbus.read_input_registers(register_addr=33022, quantity=6))

    See :doc:`examples` directory for further examples.

    """

    def __init__(self, address, serial, **kwargs):
        """Constructor"""

        self.address = address
        self.serial = serial

        self.port = kwargs.get("port", 8899)
        self.mb_slave_id = kwargs.get("mb_slave_id", 1)
        self.verbose = kwargs.get("verbose", False)
        self.socket_timeout = kwargs.get("socket_timeout", 60)
        self.v5_error_correction = kwargs.get("error_correction", False)

        self._v5_frame_def()
        self.sock = self._create_socket()

    def _v5_frame_def(self):
        """Define and contruct V5 request frame structure."""
        self.v5_start = bytes.fromhex("A5")
        self.v5_length = bytes.fromhex("0000")  # placeholder value
        self.v5_controlcode = struct.pack("<H",0x4510)
        self.v5_serial = bytes.fromhex("0000")
        self.v5_loggerserial = struct.pack("<I", self.serial)
        self.v5_frametype = bytes.fromhex("02")
        self.v5_sensortype = bytes.fromhex("0000")
        self.v5_deliverytime = bytes.fromhex("00000000")
        self.v5_powerontime = bytes.fromhex("00000000")
        self.v5_offsettime = bytes.fromhex("00000000")
        self.v5_checksum = bytes.fromhex("00")  # placeholder value
        self.v5_end = bytes.fromhex("15")

    @staticmethod
    def _calculate_v5_frame_checksum(frame):
        """Calculate checksum on all frame bytes except head, end and checksum

        :param frame: V5 frame
        :type frame: bytearray
        :return: Checksum value of V5 frame
        :rtype: int

        """
        checksum = 0
        for i in range(1, len(frame) - 2, 1):
            checksum += frame[i] & 0xFF
        return int((checksum & 0xFF))

    def _v5_frame_encoder(self, modbus_frame):
        """Take a modbus RTU frame and encode it in a V5 data logging stick frame

        :param modbus_frame: Modbus RTU frame
        :type modbus_frame: bytes
        :return: V5 frame
        :rtype: bytearray

        """

        self.v5_length = struct.pack("<H", 15 + len(modbus_frame))

        v5_header = bytearray(
            self.v5_start
            + self.v5_length
            + self.v5_controlcode
            + self.v5_serial
            + self.v5_loggerserial
        )

        v5_payload = bytearray(
            self.v5_frametype
            + self.v5_sensortype
            + self.v5_deliverytime
            + self.v5_powerontime
            + self.v5_offsettime
            + modbus_frame
        )

        v5_trailer = bytearray(self.v5_checksum + self.v5_end)

        v5_frame = v5_header + v5_payload + v5_trailer

        v5_frame[len(v5_frame) - 2] = self._calculate_v5_frame_checksum(v5_frame)
        return v5_frame

    def _v5_frame_decoder(self, v5_frame):
        """Decodes a V5 data logging stick frame and returns a modbus RTU frame

        Modbus RTU frame will start at position 25 through ``len(v5_frame)-2``.

        Occasionally logger can send a spurious 'keep-alive' reply with a
        control code of ``0x4710``. These messages can either take the place of,
        or be appended to valid ``0x1510`` responses. In this case, the v5_frame
        will contain an invalid checksum.

        Validate the following:

        1) V5 start and end are correct (``0xA5`` and ``0x15`` respectively)
        2) V5 checksum is correct
        3) V5 data logger serial number is correct (in most (all?) instances the
           reply is correct, but request can obviously be incorrect)
        4) V5 control code is correct (``0x1510``)
        5) v5_frametype contains the correct value (``0x02`` in byte 11)
        6) Modbus RTU frame length is at least 5 bytes (vast majority of RTU
           frames will be >=6 bytes, but valid 5 byte error/exception RTU frames
           are possible)

        :param v5_frame: V5 frame
        :type frame: bytes
        :return: Modbus RTU Frame
        :rtype: bytes
        :raises V5FrameError: If parsing fails due to invalid V5 frame

        """
        frame_len = len(v5_frame)
        (payload_len,) = struct.unpack("<H", v5_frame[1:3])

        frame_len_without_payload_len = 13

        if frame_len != (frame_len_without_payload_len + payload_len):
            if self.verbose:
                print("frame_len does not match payload_len.")
            if self.v5_error_correction:
                frame_len = frame_len_without_payload_len + payload_len

        if (v5_frame[0] != int.from_bytes(self.v5_start, byteorder="big")) or (
            v5_frame[frame_len - 1] != int.from_bytes(self.v5_end, byteorder="big")
        ):
            raise V5FrameError("V5 frame contains invalid start or end values")
        if v5_frame[frame_len - 2] != self._calculate_v5_frame_checksum(v5_frame):
            raise V5FrameError("V5 frame contains invalid V5 checksum")
        if v5_frame[7:11] != self.v5_loggerserial:
            raise V5FrameError("V5 frame contains incorrect data logger serial number")
        if v5_frame[3:5] != struct.pack("<H",0x1510):
            raise V5FrameError("V5 frame contains incorrect control code")
        if v5_frame[11] != int("02", 16):
            raise V5FrameError("V5 frame contains invalid frametype")

        modbus_frame = v5_frame[25 : frame_len - 2]

        if len(modbus_frame) < 5:
            raise V5FrameError("V5 frame does not contain a valid Modbus RTU frame")

        return modbus_frame

    def _send_receive_v5_frame(self, data_logging_stick_frame):
        """Send v5 frame to the data logger and receive response

        :param data_logging_stick_frame: V5 frame to transmit
        :type frame: bytes
        :return: V5 frame received
        :rtype: bytes

        """
        if self.verbose:
            print("SENT: " + data_logging_stick_frame.hex(" "))

        self.sock.sendall(data_logging_stick_frame)
        v5_response = self.sock.recv(1024)

        if self.verbose:
            print("RECD: " + v5_response.hex(" "))
        return v5_response

    def _send_receive_modbus_frame(self, mb_request_frame):
        """Encodes mb_frame, sends/receives v5_frame, decodes response

        :param mb_request_frame: Modbus RTU frame to transmit
        :type frame: bytes
        :return: Modbus RTU frame received
        :rtype: bytes

        """
        v5_request_frame = self._v5_frame_encoder(mb_request_frame)
        v5_response_frame = self._send_receive_v5_frame(v5_request_frame)
        mb_response_frame = self._v5_frame_decoder(v5_response_frame)
        return mb_response_frame

    def _get_modbus_response(self, mb_request_frame):
        """Returns mb response values for a given mb_request_frame

        :param mb_request_frame: Modbus RTU frame to parse
        :type frame: bytes
        :return: Modbus RTU decoded values
        :rtype: list[int]

        """
        mb_response_frame = self._send_receive_modbus_frame(mb_request_frame)
        modbus_values = rtu.parse_response_adu(mb_response_frame, mb_request_frame)
        return modbus_values

    def _create_socket(self):
        """Creates and returns a socket"""
        sock = socket.create_connection((self.address, self.port), self.socket_timeout)
        return sock

    @staticmethod
    def twos_complement(val, num_bits):
        """Calculate 2s Complement

        :param val: Value to calculate
        :type val: int
        :param bits: Number of bits
        :type bits: int

        :return: 2s Complement value
        :rtype: int

        """
        if val < 0:
            val = (1 << num_bits) + val
        else:
            if val & (1 << (num_bits - 1)):
                val = val - (1 << num_bits)
        return val

    def _format_response(self, modbus_values, **kwargs):
        """Formats a list of modbus register values (16 bits each) as a single value

        :param modbus_values: Modbus register values
        :type modbus_values: list[int]
        :param scale: Scaling factor
        :type scale: int
        :param signed: Signed value (2s complement)
        :type signed: bool
        :param bitmask: Bitmask value
        :type bitmask: int
        :param bitshift: Bitshift value
        :type bitshift: int
        :return: Formatted register value
        :rtype: int

        """
        scale = kwargs.get("scale", 1)
        signed = kwargs.get("signed", False)
        bitmask = kwargs.get("bitmask", None)
        bitshift = kwargs.get("bitshift", None)
        response = 0
        num_registers = len(modbus_values)

        for i, j in zip(range(num_registers), range(num_registers - 1, -1, -1)):
            response += modbus_values[i] << (j * 16)
        if signed:
            response = self.twos_complement(response, num_registers * 16)
        if scale != 1:
            response *= scale
        if bitmask is not None:
            response &= bitmask
        if bitshift is not None:
            response >>= bitshift

        return response

    def read_input_registers(self, register_addr, quantity):
        """Read input registers from modbus slave (Modbus function code 4)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int

        :return: List containing register values
        :rtype: list[int]

        """
        mb_request_frame = rtu.read_input_registers(
            self.mb_slave_id, register_addr, quantity
        )
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def read_holding_registers(self, register_addr, quantity):
        """Read holding registers from modbus slave (Modbus function code 3)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int

        :return: List containing register values
        :rtype: list[int]

        """
        mb_request_frame = rtu.read_holding_registers(
            self.mb_slave_id, register_addr, quantity
        )
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def read_input_register_formatted(self, register_addr, quantity, **kwargs):
        """Read input registers from modbus slave and format as single value (Modbus function code 4)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int
        :param scale: Scaling factor
        :type scale: int
        :param signed: Signed value (2s complement)
        :type signed: bool
        :param bitmask: Bitmask value
        :type bitmask: int
        :param bitshift: Bitshift value
        :type bitshift: int
        :return: Formatted register value
        :rtype: int

        """
        modbus_values = self.read_input_registers(register_addr, quantity)
        value = self._format_response(modbus_values, **kwargs)
        return value

    def read_holding_register_formatted(self, register_addr, quantity, **kwargs):
        """Read holding registers from modbus slave and format as single value (Modbus function code 3)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int
        :param scale: Scaling factor
        :type scale: int
        :param signed: Signed value (2s complement)
        :type signed: bool
        :param bitmask: Bitmask value
        :type bitmask: int
        :param bitshift: Bitshift value
        :type bitshift: int
        :return: Formatted register value
        :rtype: int

        """
        modbus_values = self.read_holding_registers(register_addr, quantity)
        value = self._format_response(modbus_values, **kwargs)
        return value

    def write_holding_register(self, register_addr, value, **kwargs):
        """Write a single holding register to modbus slave (Modbus function code 6)

        :param register_addr: Modbus register address
        :type register_addr: int
        :param value: value to write
        :type value: int
        :return: value written
        :rtype: int

        """
        mb_request_frame = rtu.write_single_register(
            self.mb_slave_id, register_addr, value
        )
        value = self._get_modbus_response(mb_request_frame)
        return value

    def write_multiple_holding_registers(self, register_addr, values):
        """Write list of multiple values to series of holding registers on modbus slave (Modbus function code 16)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param values: values to write
        :type values: list[int]
        :return: values written
        :rtype: list[int]

        """
        mb_request_frame = rtu.write_multiple_registers(
            self.mb_slave_id, register_addr, values
        )
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def read_coils(self, register_addr, quantity):
        """Read coils from modbus slave and return list of coil values (Modbus function code 1)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int
        :return: register values
        :rtype: list[int]

        """
        mb_request_frame = rtu.read_coils(self.mb_slave_id, register_addr, quantity)
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def read_discrete_inputs(self, register_addr, quantity):
        """Read discrete inputs from modbus slave and return list of input values (Modbus function code 2)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param quantity: Number of registers to query
        :type quantity: int
        :return: register values
        :rtype: list[int]

        """
        mb_request_frame = rtu.read_discrete_inputs(
            self.mb_slave_id, register_addr, quantity
        )
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def write_single_coil(self, register_addr, value):
        """Write single coil value to modbus slave (Modbus function code 5)

        :param register_addr: Modbus register start address
        :type register_addr: int
        :param value: value to write; ``0xFF00`` (On) or ``0x0000`` (Off)
        :type value: int
        :return: value written
        :rtype: int

        """
        mb_request_frame = rtu.write_single_coil(self.mb_slave_id, register_addr, value)
        modbus_values = self._get_modbus_response(mb_request_frame)
        return modbus_values

    def send_raw_modbus_frame(self, mb_request_frame):
        """Send raw modbus frame and return modbus response frame

        Wrapper around internal method :func:`_send_receive_modbus_frame() <pysolarmanv5.PySolarmanV5._send_receive_modbus_frame>`

        :param frame: Modbus frame
        :type frame: bytearray
        :return: Modbus frame
        :rtype: bytearray

        """
        return self._send_receive_modbus_frame(mb_request_frame)

    def send_raw_modbus_frame_parsed(self, mb_request_frame):
        """Send raw modbus frame and return parsed modbusresponse list

        Wrapper around internal method :func:`_get_modbus_response() <pysolarmanv5.PySolarmanV5._get_modbus_response>`

        :param frame: Modbus frame
        :type frame: bytearray
        :return: Modbus RTU decoded values
        :rtype: list[int]
        """
        return self._get_modbus_response(mb_request_frame)
