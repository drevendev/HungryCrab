using FluentAssertions;
using Xunit;

namespace Crustacean.Tests;

public class ClawTests
{
    [Fact]
    public void Squeeze_adds_strength()
    {
        new Claw(2).Squeeze(3).Strength.Should().Be(5);
    }

    [Fact]
    public void Negative_strength_is_rejected()
    {
        var act = () => new Claw(-1);
        act.Should().Throw<ArgumentOutOfRangeException>();
    }
}
