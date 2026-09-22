const Player = require('./models/musician');

function applyExtraSetup(sequelize) {
  const { instrument, orchestra, venue, ghost } = sequelize.models;

  orchestra.hasMany(instrument);
  instrument.belongsTo(orchestra);
  orchestra.hasMany(venue);
  Player.belongsTo(orchestra);
  Player.belongsToMany(venue, { through: 'booking' });
  ghost.belongsTo(orchestra);
}

module.exports = { applyExtraSetup };
