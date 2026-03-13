// osu_pp_bridge — osu! PP calculator bridge for Python
//
// Reads JSON from stdin, calculates PP using ppy osu! libraries, writes JSON to stdout.
//
// Input:
// {
//   "beatmap_path": "/abs/path/to/file.osu",
//   "lazer": true,
//   "combo": 2013,
//   "n300": 1607, "n100": 1, "n50": 0, "misses": 6,
//   "slider_end_hits": 555,
//   "large_tick_hits": 699,
//   "small_tick_hits": 556,
//   "mods": [
//     { "acronym": "DT", "settings": { "speed_change": 1.7 } },
//     { "acronym": "HD" },
//     { "acronym": "DA", "settings": { "approach_rate": 9.1, "overall_difficulty": 0,
//                                      "circle_size": 0, "drain_rate": 0 } },
//     { "acronym": "RX" }
//   ]
// }
//
// Output:
// { "pp": 1318.6, "stars": 11.56, "aim": 1156.7, "speed": 0, "accuracy": 0, "flashlight": 0 }

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Formats;
using osu.Game.IO;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Scoring;
using osu.Game.Scoring;

// ── Parse stdin ───────────────────────────────────────────────────────────────
var raw   = Console.In.ReadToEnd();
var input = JsonNode.Parse(raw)!;

var beatmapPath   = input["beatmap_path"]!.GetValue<string>();
var isLazer       = input["lazer"]?.GetValue<bool>()    ?? false;
var combo         = input["combo"]?.GetValue<int>()     ?? 0;
var n300          = input["n300"]?.GetValue<int>()      ?? 0;
var n100          = input["n100"]?.GetValue<int>()      ?? 0;
var n50           = input["n50"]?.GetValue<int>()       ?? 0;
var misses        = input["misses"]?.GetValue<int>()    ?? 0;
var sliderEndHits = input["slider_end_hits"]?.GetValue<int>() ?? 0;
var largeTickHits = input["large_tick_hits"]?.GetValue<int>() ?? 0;
var smallTickHits = input["small_tick_hits"]?.GetValue<int>() ?? 0;

// ── Load beatmap ──────────────────────────────────────────────────────────────
LegacyBeatmapDecoder.Register();

Beatmap rawBeatmap;
using (var fileStream = File.OpenRead(beatmapPath))
using (var reader = new LineBufferedReader(fileStream))
{
    var decoder = Decoder.GetDecoder<Beatmap>(reader);
    fileStream.Position = 0;
    rawBeatmap = decoder.Decode(new LineBufferedReader(fileStream));
}

var workingBeatmap = new FlatWorkingBeatmap(rawBeatmap);

// ── Parse mods ────────────────────────────────────────────────────────────────
var ruleset    = new OsuRuleset();
var allMods    = ruleset.AllMods.OfType<Mod>()
    .ToDictionary(m => m.Acronym.ToUpperInvariant(), m => m);
var activeMods = new List<Mod>();

var modsArray = input["mods"]?.AsArray();
if (modsArray != null)
{
    foreach (var modNode in modsArray)
    {
        if (modNode == null) continue;
        var acronym = modNode["acronym"]!.GetValue<string>().ToUpperInvariant();
        if (!allMods.TryGetValue(acronym, out var template)) continue;

        var mod      = template.DeepClone();
        var settings = modNode["settings"]?.AsObject();

        if (settings != null)
        {
            if (mod is ModRateAdjust rateAdj && settings["speed_change"] is { } sc)
                rateAdj.SpeedChange.Value = sc.GetValue<double>();

            // Must cast to OsuModDifficultyAdjust — base class doesn't expose AR/CS/etc.
            if (mod is OsuModDifficultyAdjust da)
            {
                if (settings["approach_rate"] is { } ar)
                    da.ApproachRate.Value = (float)ar.GetValue<double>();
                if (settings["overall_difficulty"] is { } od)
                    da.OverallDifficulty.Value = (float)od.GetValue<double>();
                if (settings["circle_size"] is { } cs)
                    da.CircleSize.Value = (float)cs.GetValue<double>();
                if (settings["drain_rate"] is { } hp)
                    da.DrainRate.Value = (float)hp.GetValue<double>();
            }
        }

        activeMods.Add(mod);
    }
}

// ── Build ScoreInfo ───────────────────────────────────────────────────────────
var scoreInfo = new ScoreInfo(workingBeatmap.BeatmapInfo, ruleset.RulesetInfo)
{
    MaxCombo      = combo,
    Mods          = activeMods.ToArray(),
    IsLegacyScore = !isLazer,
    Passed        = true,
    Accuracy      = input["accuracy"]?.GetValue<double>() ?? 1.0,
};

scoreInfo.Statistics[HitResult.Great] = n300;
scoreInfo.Statistics[HitResult.Ok]    = n100;
scoreInfo.Statistics[HitResult.Meh]   = n50;
scoreInfo.Statistics[HitResult.Miss]  = misses;

if (isLazer)
{
    scoreInfo.Statistics[HitResult.SliderTailHit] = sliderEndHits;
    scoreInfo.Statistics[HitResult.LargeTickHit]  = largeTickHits;
    scoreInfo.Statistics[HitResult.SmallTickHit]  = smallTickHits;

    var converted = workingBeatmap.GetPlayableBeatmap(ruleset.RulesetInfo, activeMods);
    var maxStats = converted.HitObjects
        .SelectMany(h => h.NestedHitObjects.Append(h))
        .Select(h => h.CreateJudgement().MaxResult)
        .GroupBy(r => r)
        .ToDictionary(g => g.Key, g => g.Count());

    foreach (var (hitResult, count) in maxStats)
        scoreInfo.MaximumStatistics[hitResult] = count;
}

// ── Calculate ─────────────────────────────────────────────────────────────────
var diffCalc  = ruleset.CreateDifficultyCalculator(workingBeatmap);
var diffAttrs = diffCalc.Calculate(activeMods.ToArray());
var perfCalc  = ruleset.CreatePerformanceCalculator()!;
var perfAttrs = perfCalc.Calculate(scoreInfo, diffAttrs);

// ── Output ────────────────────────────────────────────────────────────────────
var osuAttrs = perfAttrs as osu.Game.Rulesets.Osu.Difficulty.OsuPerformanceAttributes;

var result = new
{
    pp         = perfAttrs.Total,
    stars      = diffAttrs.StarRating,
    aim        = osuAttrs?.Aim        ?? 0,
    speed      = osuAttrs?.Speed      ?? 0,
    accuracy   = osuAttrs?.Accuracy   ?? 0,
    flashlight = osuAttrs?.Flashlight ?? 0,
};

Console.WriteLine(JsonSerializer.Serialize(result));
