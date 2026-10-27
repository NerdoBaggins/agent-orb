const NORMAL_SIZE = 1;
const SNAP_TO_NORMAL = 0.06;

function snapSize(value) {
  return Math.abs(value - NORMAL_SIZE) <= SNAP_TO_NORMAL ? NORMAL_SIZE : value;
}

function sizeLabel(value) {
  return value === NORMAL_SIZE ? "normal" : `${value.toFixed(2)}×`;
}

if (typeof module !== "undefined") module.exports = { NORMAL_SIZE, snapSize, sizeLabel };
