const { DataTypes } = require('sequelize');
const sequelize = require('./db');

var Polka = sequelize.define('mazurka', { bars: DataTypes.INTEGER });
var Polka = sequelize.define('polka', { bars: DataTypes.INTEGER });

if (sequelize) {
  var Reel = sequelize.define('reel', { bars: DataTypes.INTEGER });
}

try {
  var Hornpipe = sequelize.define('hornpipe', { bars: DataTypes.INTEGER });
} catch (error) {}

function wire(s) {
  const { orchestra } = s.models;
  Polka.belongsTo(orchestra);
  Reel.belongsTo(Polka);
}

module.exports = { Hornpipe, wire };
