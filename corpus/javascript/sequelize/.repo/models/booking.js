const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const Booking = sequelize.define('gig', {
  musicianId: DataTypes.INTEGER.UNSIGNED,
  venueId: { type: DataTypes.BIGINT.UNSIGNED.ZEROFILL },
});

module.exports = { Booking };
