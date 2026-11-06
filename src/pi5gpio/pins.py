"""Raspberry Pi 40-pin header description (Pi 5 uses the same layout as earlier models)."""
from dataclasses import dataclass
from typing import Optional

# Physical pin -> (label, BCM number or None for fixed pins, special function or None)
_HEADER = {
    1: ("3V3", None, None), 2: ("5V", None, None),
    3: ("GPIO2", 2, "I2C1 SDA"), 4: ("5V", None, None),
    5: ("GPIO3", 3, "I2C1 SCL"), 6: ("GND", None, None),
    7: ("GPIO4", 4, None), 8: ("GPIO14", 14, "UART TXD"),
    9: ("GND", None, None), 10: ("GPIO15", 15, "UART RXD"),
    11: ("GPIO17", 17, None), 12: ("GPIO18", 18, None),
    13: ("GPIO27", 27, None), 14: ("GND", None, None),
    15: ("GPIO22", 22, None), 16: ("GPIO23", 23, None),
    17: ("3V3", None, None), 18: ("GPIO24", 24, None),
    19: ("GPIO10", 10, "SPI0 MOSI"), 20: ("GND", None, None),
    21: ("GPIO9", 9, "SPI0 MISO"), 22: ("GPIO25", 25, None),
    23: ("GPIO11", 11, "SPI0 SCLK"), 24: ("GPIO8", 8, "SPI0 CE0"),
    25: ("GND", None, None), 26: ("GPIO7", 7, "SPI0 CE1"),
    27: ("ID_SD", None, "HAT ID EEPROM"), 28: ("ID_SC", None, "HAT ID EEPROM"),
    29: ("GPIO5", 5, None), 30: ("GND", None, None),
    31: ("GPIO6", 6, None), 32: ("GPIO12", 12, None),
    33: ("GPIO13", 13, None), 34: ("GND", None, None),
    35: ("GPIO19", 19, None), 36: ("GPIO16", 16, None),
    37: ("GPIO26", 26, None), 38: ("GPIO20", 20, None),
    39: ("GND", None, None), 40: ("GPIO21", 21, None),
}

# BCM pins that can output hardware PWM on the Pi 5 (RP1 PWM0 channels).
HW_PWM_CHANNELS = {12: 0, 13: 1, 18: 2, 19: 3}


@dataclass(frozen=True)
class Pin:
    physical: int
    label: str
    bcm: Optional[int]
    special: Optional[str]

    @property
    def controllable(self) -> bool:
        return self.bcm is not None

    @property
    def kind(self) -> str:
        if self.label == "GND":
            return "gnd"
        if self.label == "3V3":
            return "3v3"
        if self.label == "5V":
            return "5v"
        if self.bcm is None:
            return "id"
        return "gpio"


PINS = [Pin(n, *_HEADER[n]) for n in range(1, 41)]
BY_PHYSICAL = {p.physical: p for p in PINS}
BY_BCM = {p.bcm: p for p in PINS if p.bcm is not None}
BCM_PINS = sorted(BY_BCM)


def special_function(bcm: int) -> Optional[str]:
    return BY_BCM[bcm].special
