using FluentAssertions;
using Xunit;

namespace Crustacean.Tests;

public class ShellTests
{
    [Fact]
    public void Harden_ignores_blank_layers()
    {
        var shell = new Shell();
        shell.Harden(null);
        shell.Harden("  ");
        shell.Harden("calcium");
        shell.Layers.Should().ContainSingle().Which.Should().Be("calcium");
    }
}
