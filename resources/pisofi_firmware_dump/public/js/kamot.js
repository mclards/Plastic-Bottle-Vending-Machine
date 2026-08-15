(function (global, factory) {
  typeof exports === "object" && typeof module !== "undefined"
    ? factory(exports)
    : typeof define === "function" && define.amd
    ? define(["exports"], factory)
    : ((global = global || self), factory((global.Kamot = {})));
})(this, function (exports) {
  "use strict";

var Kamot = Kamot || {};

/**
     * Basic helper to set a cookie.
     * @param {string} name  - Cookie name
     * @param {string} value - Cookie value
     * @param {number} days  - Number of days until expiration
     */
function setCookie(name, value, days) {
  var date = new Date();
  date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
  var expires = "expires=" + date.toUTCString();
  document.cookie = name + "=" + encodeURIComponent(value) + ";" + expires + ";path=/";
}

/**
 * Basic helper to read a cookie by name.
 * Returns the cookie value or null if not found.
 */
function getCookie(name) {
  var nameEQ = name + "=";
  var ca = document.cookie.split(';');
  for (var i = 0; i < ca.length; i++) {
    var c = ca[i].trim();
    if (c.indexOf(nameEQ) === 0) {
      return decodeURIComponent(c.substring(nameEQ.length, c.length));
    }
  }
  return null;
}

/**
 * A simple random ID generator (e.g. pseudo-UUID).
 * For a more standard approach, you can implement RFC4122-compliant UUID.
 */
function generateRandomId() {
  // In a real app, consider using a robust UUID library like 'uuid'.
  // This is a simplified example:
  return 'xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0;
    var v = (c === 'x') ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

/**
 * Converts an ArrayBuffer to a hex string.
 */
function bufferToHex(buffer) {
  var byteArray = new Uint8Array(buffer);
  var hexCodes = [];
  for (var i = 0; i < byteArray.length; i++) {
    var value = byteArray[i];
    var hexCode = value.toString(16).padStart(2, '0');
    hexCodes.push(hexCode);
  }
  return hexCodes.join('');
}

/**
 * A fallback hash (NOT cryptographically secure).
 * Used if SubtleCrypto is unavailable (like in older or captive-portal browsers).
 */
function fallbackHash(str) {
  var hash = 0;
  if (str.length === 0) return hash.toString();
  for (var i = 0; i < str.length; i++) {
    var chr = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + chr;
    hash |= 0; // Convert to 32-bit int
  }
  return (hash >>> 0).toString(16);
}

/**
 * Tries to do a SHA-256 hash using Web Crypto API if available.
 * Otherwise, falls back to the naive hash.
 */
function sha256OrFallback(message) {
  return new Promise(function(resolve, reject) {
    if (window.crypto && window.crypto.subtle && window.TextEncoder) {
      try {
        var encoder = new TextEncoder();
        var msgBuffer = encoder.encode(message);
        window.crypto.subtle.digest('SHA-256', msgBuffer)
          .then(function(hashBuffer) {
            resolve(bufferToHex(hashBuffer));
          })
          .catch(function(err) {
            console.warn('Digest error, fallback:', err);
            resolve(fallbackHash(message));
          });
      } catch (e) {
        console.warn('TextEncoder error, fallback:', e);
        resolve(fallbackHash(message));
      }
    } else {
      // Fallback route
      resolve(fallbackHash(message));
    }
  });
}

/**
 * Gather a minimal set of data points for environment-based fingerprint.
 */
function gatherBrowserData() {
  var data = {};

  data.userAgent = (navigator && navigator.userAgent) ? navigator.userAgent : 'UnknownUA';
  data.screenResolution = (screen) ? (screen.width + 'x' + screen.height) : '0x0';
  data.colorDepth = (screen) ? screen.colorDepth : 'N/A';
  data.timezoneOffset = (new Date()).getTimezoneOffset();
  data.language = (navigator && navigator.language) ? navigator.language : 'UnknownLang';
  data.platform = (navigator && navigator.platform) ? navigator.platform : 'UnknownPlatform';

  return data;
}

/**
 * Creates a stable string from the data object.
 */
function dataToString(data) {
  return [
    data.deviceId || 'UnknownID',
    data.userAgent,
    data.screenResolution,
    data.colorDepth,
    data.timezoneOffset,
    data.language,
    data.platform
  ].join('||');
}

/**
 * Generate an environment fingerprint (hashed), or fallback if needed.
 */
function generateEnvFingerprint(deviceId) {
  var data = gatherBrowserData();
  data.deviceId = deviceId || 'UnknownID';
  var dataStr = dataToString(data);
  return sha256OrFallback(dataStr);
}

/**
 * Main function that:
 * 1. Checks for a 'deviceId' cookie.
 * 2. If not found, create a new random ID and store it in the cookie.
 * 3. (Optional) generate an environment-based fingerprint (just to show how you might combine them).
 * 4. Return the final device ID.
 */
function getOrCreateDeviceId() {
  return new Promise(function(resolve, reject) {
    var storedDeviceId = getCookie('xpfideviceId');
    if (storedDeviceId) {
      // Cookie found: just return it
      resolve(storedDeviceId);
    } else {
      // No cookie: create a new random ID
      var newId = generateRandomId();
      // Store it in the cookie (e.g., for 365 days)
      setCookie('xpfideviceId', newId, 365);

      resolve(newId);
    }
  });
}

function generateFingerprint() {
  return getOrCreateDeviceId()
    .then(function(deviceId) {
      return generateEnvFingerprint(deviceId);
    });
}

Kamot.generateFingerprint = generateFingerprint;
window.Kamot = Kamot;

exports.fn = Kamot;

Object.defineProperty(exports, "__esModule", { value: true });
});
