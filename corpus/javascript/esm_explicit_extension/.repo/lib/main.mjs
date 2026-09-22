import { go } from './mod.js';
import * as mod from './mod.js';

export function run() {
  return go() + mod.go();
}
