namespace Crustacean;

/// <summary>A claw with a grip strength.</summary>
public sealed class Claw
{
    public Claw(int strength)
    {
        if (strength < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(strength));
        }

        Strength = strength;
    }

    public int Strength { get; }

    public Claw Squeeze(int extra) => new(checked(Strength + extra));
}
