const Player = require('./models/musician');
const { Booking } = require('./models/booking');

function applyExtraSetup(sequelize) {
  const { instrument, orchestra, venue, ghost } = sequelize.models;

  orchestra.hasMany(instrument);
  instrument.belongsTo(orchestra);
  orchestra.hasMany(venue);
  Player.belongsTo(orchestra);
  Player.belongsToMany(venue, { through: 'booking' });
  ghost.belongsTo(orchestra);
  Booking.belongsTo(orchestra);
}

module.exports = { applyExtraSetup };
