// Single import point for the vendored Preact + htm (no build step, no bare specifiers).
import { h, render, Fragment } from '../vendor/preact.module.js';
import {
  useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback,
} from '../vendor/hooks.module.js';
import htm from '../vendor/htm.module.js';

export const html = htm.bind(h);
export { h, render, Fragment, useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback };
