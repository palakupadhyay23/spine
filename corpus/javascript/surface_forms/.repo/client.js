const rflx = require('./rflx');
const declared = require('./declared');
const thisy = require('./thisy');
const proto = require('./proto');
const created = require('./created');
const getter = require('./getter');
const dead = require('./dead');
const util = require('./util');

function go() {
  rflx.find();
  rflx.secret();
  declared.list();
  declared.secret();
  thisy.find();
  thisy.secret();
  proto.find();
  proto.other();
  created.find();
  getter.find();
  getter.secret();
  dead.stale();
  return util.util();
}

function later() {
  const Holder = class {
    static {
      var rflx = 1;
    }
  };
  return rflx.find();
}
