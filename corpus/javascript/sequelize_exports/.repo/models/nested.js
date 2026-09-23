const { DataTypes } = require('sequelize');
const sequelize = require('./db');

exports.api = {};
exports.api.Lyric = sequelize.define('verse', {
  text: DataTypes.STRING,
});
mixin(exports);
