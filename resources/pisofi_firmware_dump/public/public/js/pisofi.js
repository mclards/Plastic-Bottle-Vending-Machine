
(function (global, factory) {
    typeof exports === 'object' && typeof module !== 'undefined' ? factory(exports) :
    typeof define === 'function' && define.amd ? define(['exports'], factory) :
    (global = global || self, factory(global.Pisofi = {}));
  }(this, function (exports) { 'use strict';

  var Pisofi = {};

  function secondsToTime(time) {
   
    var secondsInAMinute = 60,
        secondsInAnHour  = 60 * secondsInAMinute,
        secondsInADay    = 24 * secondsInAnHour,

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
        'days' : days,
        'hours' : hours,
        'minutes' : minutes,
        'seconds' : seconds,
    };
    return obj;
  }

  var Pisofi = {
    secondsToTime : secondsToTime
  };

  exports.fn = Pisofi;

  Object.defineProperty(exports, '__esModule', { value: true });

}));