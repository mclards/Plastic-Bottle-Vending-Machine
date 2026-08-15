(function (global, factory) {
  typeof exports === "object" && typeof module !== "undefined"
    ? factory(exports)
    : typeof define === "function" && define.amd
    ? define(["exports"], factory)
    : ((global = global || self), factory((global.Pisofi = {})));
})(this, function (exports) {
  "use strict";

  var Pisofi = {};

  function secondsToTime(time) {
    var secondsInAMinute = 60,
      secondsInAnHour = 60 * secondsInAMinute,
      secondsInADay = 24 * secondsInAnHour,
      // extract days
      days = Math.floor(time / secondsInADay),
      // extract hours
      hourSeconds = time % secondsInADay,
      hours = Math.floor(hourSeconds / secondsInAnHour),
      // extract minutes
      minuteSeconds = hourSeconds % secondsInAnHour,
      minutes = Math.floor(minuteSeconds / secondsInAMinute),
      // extract the remaining seconds
      remainingSeconds = minuteSeconds % secondsInAMinute,
      seconds = Math.ceil(remainingSeconds);

    // return the final array
    var obj = {
      days: days,
      hours: hours,
      minutes: minutes,
      seconds: seconds,
    };
    return obj;
  }

  function keepAlive(interval) {
    var self = this;
    interval = interval || 30;
    interval = interval * 1000;
    setInterval(function () {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/keepalive");
      xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.onload = function () {
        if (xhr.status === 200) {
          var response = JSON.parse(xhr.responseText);
          self.csrf.name = response.token.csrf_name;
          self.csrf.value = response.token.csrf_value;
        } else if (xhr.status !== 200) {
        }
      };
      xhr.send(
        "csrf_name=" + self.csrf.name + "&csrf_value=" + self.csrf.value
      );
    }, interval);
  }

  var formatTime = function (time) {
    var t = secondsToTime(time);
    var timeMap = {
      days: "d",
      hours: "h",
      minutes: "m",
      seconds: "s",
    };

    var data = [],
      keys = Object.keys(timeMap);

    keys.forEach(function (k) {
      if (t[k] > 0) {
        data.push(t[k] + timeMap[k]);
      }
    });
    if (data.length === 0) {
      return "0";
    }
    if (data.length === 1) {
      return data[0];
    }
    return data.splice(0, data.length - 1).join(",") + " and " + data[0];
  };

  function roundTo(n, digits) {
    if (digits === undefined) {
      digits = 0;
    }

    var multiplicator = Math.pow(10, digits);
    n = parseFloat((n * multiplicator).toFixed(11));
    return (Math.round(n) / multiplicator).toFixed(2);
  }

  function formatBits(bytes, precision, as_object) {
    precision = precision || 2;
    as_object = as_object || false;

    var units = ["B", "KB", "MB", "GB", "TB"];
    bytes = Math.max(bytes, 0);
    var pow = Math.floor((bytes ? Math.log(bytes) : 0) / Math.log(1000));
    pow = Math.min(pow, units.length - 1);
    bytes /= Math.pow(1000, pow);

    if (as_object) {
      return {
        value: Math.round(bytes, precision),
        unit: units[pow].toLowerCase(),
      };
    }

    return roundTo(bytes, precision) + " " + units[pow];
  }

  function formatSpeed(bytes, precision, as_object) {
    precision = precision || 2;
    as_object = as_object || false;

    var units = ["Kbps", "Mbps", "Gbps", "Tbps"];
    bytes = Math.max(bytes, 0);
    var pow = Math.floor((bytes ? Math.log(bytes) : 0) / Math.log(1000));
    pow = Math.min(pow, units.length - 1);
    bytes /= Math.pow(1000, pow);

    if (as_object) {
      return {
        value: Math.round(bytes, precision),
        unit: units[pow].toLowerCase(),
      };
    }
    return roundTo(bytes, precision) + " " + units[pow];
  }

  var throttle = function (func, wait, immediate) {
    var timeout;
    return function () {
      var context = this,
        args = arguments;
      var later = function () {
        timeout = null;
        if (!immediate) func.apply(context, args);
      };
      var callNow = immediate && !timeout;
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
      if (callNow) func.apply(context, args);
    };
  };

  function long2ip(ip) {
    if (!isFinite(ip)) {
      return false;
    }
    return [
      (ip >>> 24) & 0xff,
      (ip >>> 16) & 0xff,
      (ip >>> 8) & 0xff,
      ip & 0xff,
    ].join(".");
  }
  function ip2long(argIP) {
    var i = 0;
    var pattern = new RegExp(
      [
        "^([1-9]\\d*|0[0-7]*|0x[\\da-f]+)",
        "(?:\\.([1-9]\\d*|0[0-7]*|0x[\\da-f]+))?",
        "(?:\\.([1-9]\\d*|0[0-7]*|0x[\\da-f]+))?",
        "(?:\\.([1-9]\\d*|0[0-7]*|0x[\\da-f]+))?$",
      ].join(""),
      "i"
    );
    argIP = argIP.match(pattern); // Verify argIP format.
    if (!argIP) {
      // Invalid format.
      return false;
    }
    // Reuse argIP variable for component counter.
    argIP[0] = 0;
    for (i = 1; i < 5; i += 1) {
      argIP[0] += !!(argIP[i] || "").length;
      argIP[i] = parseInt(argIP[i]) || 0;
    }
    // Continue to use argIP for overflow values.
    // PHP does not allow any component to overflow.
    argIP.push(256, 256, 256, 256);
    // Recalculate overflow of last component supplied to make up for missing components.
    argIP[4 + argIP[0]] *= Math.pow(256, 4 - argIP[0]);
    if (
      argIP[1] >= argIP[5] ||
      argIP[2] >= argIP[6] ||
      argIP[3] >= argIP[7] ||
      argIP[4] >= argIP[8]
    ) {
      return false;
    }
    return (
      argIP[1] * (argIP[0] === 1 || 16777216) +
      argIP[2] * (argIP[0] <= 2 || 65536) +
      argIP[3] * (argIP[0] <= 3 || 256) +
      argIP[4] * 1
    );
  }

  function getWsUrl(domain) {
    var ipformat =
      /^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
    var url =
      window.location.hostname == domain
        ? '10.0.0.1'
        : window.location.hostname;
    var scheme = window.location.href.indexOf('https') > -1 ? 'wss' : 'ws';
    var wsUrl = scheme + '://' + url + '/ws';
    if (url.match(ipformat)) {
      wsUrl = scheme + '://' + url + ':8080';
    }
    return wsUrl;
  }

  var Pisofi = {
    secondsToTime: secondsToTime,
    keepAlive: keepAlive,
    throttle: throttle,
    formatBits: formatBits,
    formatTime: formatTime,
    formatSpeed: formatSpeed,
    ip2long: ip2long,
    long2ip: long2ip,
    getWsUrl: getWsUrl
  };

  exports.fn = Pisofi;

  Object.defineProperty(exports, "__esModule", { value: true });
});
