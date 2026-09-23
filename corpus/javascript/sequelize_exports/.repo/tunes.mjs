import { Hymn, Carol } from './models/esm.mjs';

export function tune(sequelize) {
  const { orchestra } = sequelize.models;
  Hymn.belongsTo(orchestra);
  Carol.belongsTo(orchestra);
}
