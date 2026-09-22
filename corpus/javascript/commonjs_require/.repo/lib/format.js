function pad(value) {
  return String(value);
}

exports.pad = pad;

exports.money = function (value) {
  return "$" + pad(value);
};
