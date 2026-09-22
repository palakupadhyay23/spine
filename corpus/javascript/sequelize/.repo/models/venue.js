const { DataTypes } = require('sequelize');

module.exports = (sequelize) => {
  sequelize.define('venue', {
    id: { primaryKey: true, type: DataTypes.INTEGER },
    city: { type: DataTypes.STRING },
  });
};
