from __future__ import annotations

from abc import ABC, abstractmethod


class SensorProvider(ABC):
    """Hardware-neutral sensor interface; values are positioning metadata only."""

    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_distance_mm(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class VL53L0XSensorProvider(SensorProvider):
    """Optional VL53L0X adapter using the common Adafruit CircuitPython stack."""

    def __init__(self):
        self._sensor = None

    def initialize(self) -> None:
        try:
            import board
            import busio
            import adafruit_vl53l0x
        except ImportError as exc:
            raise RuntimeError(
                "board, busio, and adafruit_vl53l0x are required on the Raspberry Pi"
            ) from exc
        i2c = busio.I2C(board.SCL, board.SDA)
        self._sensor = adafruit_vl53l0x.VL53L0X(i2c)

    def read_distance_mm(self) -> float:
        if self._sensor is None:
            raise RuntimeError("Sensor has not been initialized")
        value = float(self._sensor.range)
        if value < 0 or value > 2000:
            raise ValueError("Sensor distance is outside the supported range")
        return value

    def close(self) -> None:
        self._sensor = None