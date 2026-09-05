using BenchmarkDotNet.Attributes;
using BenchmarkDotNet.Running;

namespace Crustacean.Benchmarks;

public class ClawBenchmarks
{
    [Benchmark]
    public Claw Squeeze() => new Claw(1).Squeeze(1);

    public static void Main(string[] args) => BenchmarkRunner.Run<ClawBenchmarks>(args: args);
}
